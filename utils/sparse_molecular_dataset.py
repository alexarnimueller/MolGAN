import pickle
import numpy as np

from rdkit import Chem

if __name__ == '__main__':
    from progress_bar import ProgressBar
    from utils import strip_salt
else:
    from utils.progress_bar import ProgressBar
    from utils.utils import strip_salt

from datetime import datetime


class SparseMolecularDataset():
    def __init__(self):
        self.__len = None
        self.data = None
        self.data_A = None
        self.data_D = None
        self.data_F = None
        self.data_Le = None
        self.data_Lv = None
        self.data_S = None
        self.data_X = None
        self.features = None
        self.smiles = None
        self.test_count = None
        self.test_counter = None
        self.test_idx = None
        self.train_count = None
        self.train_counter = None
        self.train_idx = None
        self.validation_count = None
        self.validation_counter = None
        self.validation_idx = None
        self.vertexes = None

    def load(self, filename, subset=1):
        with open(filename, 'rb') as f:
            self.__dict__.update(pickle.load(f))

        self.train_idx = np.random.choice(self.train_idx, int(len(self.train_idx) * subset), replace=False)
        self.validation_idx = np.random.choice(self.validation_idx, int(len(self.validation_idx) * subset),
                                               replace=False)
        self.test_idx = np.random.choice(self.test_idx, int(len(self.test_idx) * subset), replace=False)

        self.train_count = len(self.train_idx)
        self.validation_count = len(self.validation_idx)
        self.test_count = len(self.test_idx)

        self.__len = self.train_count + self.validation_count + self.test_count

    def save(self, filename):
        with open(filename, 'wb') as f:
            pickle.dump(self.__dict__, f)

    def generate(self, filename, add_h=False, heavyatom=9, atoms=None, size=None, validation=0.1, test=0.1):
        self.log('Extracting {}..'.format(filename))

        if filename.endswith('.sdf'):
            self.data = list(filter(lambda x: x is not None, Chem.SDMolSupplier(filename)))
        elif filename.endswith('.smi'):
            self.data = [Chem.MolFromSmiles(line) for line in open(filename, 'r').readlines()]

        self.data = list(map(Chem.AddHs, self.data)) if add_h else self.data
        self.data = list(filter(lambda x: x.GetNumAtoms() <= heavyatom, self.data))

        if atoms and isinstance(atoms, list):
            self.match_atoms(atoms)
            self.log('Filterd to molecules containing only %s' % atoms)

        self.data = strip_salt(self.data)
        self.data = self.data[:size]

        self.log('Extracted {} molecules {}adding Hydrogen!'.format(len(self.data), '' if add_h else 'not '))

        self._generate_encoders_decoders()
        self._generate_AX()

        # it contains all the molecules stored as rdkit.Chem objects
        self.data = np.array(self.data)

        # it contains all the molecules stored as SMILES strings
        self.smiles = np.array(self.smiles)

        # a (N, L) matrix where N is the length of the dataset and each L-dim vector contains the 
        # indices corresponding to a SMILE sequences with padding wrt the max length of the longest 
        # SMILES sequence in the dataset (see self._genS)
        self.data_S = np.stack(self.data_S)

        # a (N, 9, 9) tensor where N is the length of the dataset and each 9x9 matrix contains the 
        # indices of the positions of the ones in the one-hot representation of the adjacency tensor
        # (see self._genA)
        self.data_A = np.stack(self.data_A)

        # a (N, 9) matrix where N is the length of the dataset and each 9-dim vector contains the 
        # indices of the positions of the ones in the one-hot representation of the annotation matrix
        # (see self._genX)
        self.data_X = np.stack(self.data_X)

        # a (N, 9) matrix where N is the length of the dataset and each  9-dim vector contains the 
        # diagonal of the correspondent adjacency matrix
        self.data_D = np.stack(self.data_D)

        # a (N, F) matrix where N is the length of the dataset and each F vector contains features 
        # of the correspondent molecule (see self._genF)
        self.data_F = np.stack(self.data_F)

        # a (N, 9) matrix where N is the length of the dataset and each  9-dim vector contains the
        # eigenvalues of the correspondent Laplacian matrix
        self.data_Le = np.stack(self.data_Le)

        # a (N, 9, 9) matrix where N is the length of the dataset and each  9x9 matrix contains the 
        # eigenvectors of the correspondent Laplacian matrix
        self.data_Lv = np.stack(self.data_Lv)

        self.vertexes = self.data_F.shape[-2]
        self.features = self.data_F.shape[-1]

        self._generate_train_validation_test(validation, test)

    def _generate_encoders_decoders(self):
        self.log('Creating atoms encoder and decoder..')
        atom_labels = sorted(set([atom.GetAtomicNum() for mol in self.data for atom in mol.GetAtoms()] + [0]))
        self.atom_encoder_m = {l: i for i, l in enumerate(atom_labels)}
        self.atom_decoder_m = {i: l for i, l in enumerate(atom_labels)}
        self.atom_num_types = len(atom_labels)
        self.log('Created atoms encoder and decoder with {} atom types and 1 PAD symbol!'.format(
            self.atom_num_types - 1))

        self.log('Creating bonds encoder and decoder..')
        bond_labels = [Chem.rdchem.BondType.ZERO] + list(sorted(set(bond.GetBondType()
                                                                    for mol in self.data
                                                                    for bond in mol.GetBonds())))

        self.bond_encoder_m = {l: i for i, l in enumerate(bond_labels)}
        self.bond_decoder_m = {i: l for i, l in enumerate(bond_labels)}
        self.bond_num_types = len(bond_labels)
        self.log('Created bonds encoder and decoder with {} bond types and 1 PAD symbol!'.format(
            self.bond_num_types - 1))

        self.log('Creating SMILES encoder and decoder..')
        smiles_labels = ['E'] + list(set(c for mol in self.data for c in Chem.MolToSmiles(mol)))
        self.smiles_encoder_m = {l: i for i, l in enumerate(smiles_labels)}
        self.smiles_decoder_m = {i: l for i, l in enumerate(smiles_labels)}
        self.smiles_num_types = len(smiles_labels)
        self.log('Created SMILES encoder and decoder with {} types and 1 PAD symbol!'.format(
            self.smiles_num_types - 1))

    def _generate_AX(self):
        self.log('Creating features and adjacency matrices..')
        pr = ProgressBar(60, len(self.data))

        data_ax = []
        smiles = []
        data_s = []
        data_a = []
        data_x = []
        data_d = []
        data_f = []
        data_le = []
        data_lv = []

        max_length = max(mol.GetNumAtoms() for mol in self.data)
        max_length_s = max(len(Chem.MolToSmiles(mol)) for mol in self.data)

        for i, mol in enumerate(self.data):
            a = self._genA(mol, connected=True, max_length=max_length)
            d = np.count_nonzero(a, -1)
            if a is not None:
                data_ax.append(mol)
                smiles.append(Chem.MolToSmiles(mol))
                data_s.append(self._genS(mol, max_length=max_length_s))
                data_a.append(a)
                data_x.append(self._genX(mol, max_length=max_length))
                data_d.append(d)
                data_f.append(self._genF(mol, max_length=max_length))

                le, lv = np.linalg.eigh(d - a)

                data_le.append(le)
                data_lv.append(lv)

            pr.update(i + 1)

        self.log(date=False)
        self.log('Created {} features and adjacency matrices  out of {} molecules!'.format(len(data_ax),
                                                                                           len(self.data)))

        self.data = data_ax
        self.smiles = smiles
        self.data_S = data_s
        self.data_A = data_a
        self.data_X = data_x
        self.data_D = data_d
        self.data_F = data_f
        self.data_Le = data_le
        self.data_Lv = data_lv
        self.__len = len(self.data)

    def _genA(self, mol, connected=True, max_length=None):

        max_length = max_length if max_length is not None else mol.GetNumAtoms()

        a = np.zeros(shape=(max_length, max_length), dtype=np.int32)

        begin, end = [b.GetBeginAtomIdx() for b in mol.GetBonds()], [b.GetEndAtomIdx() for b in mol.GetBonds()]
        bond_type = [self.bond_encoder_m[b.GetBondType()] for b in mol.GetBonds()]

        a[begin, end] = bond_type
        a[end, begin] = bond_type

        degree = np.sum(a[:mol.GetNumAtoms(), :mol.GetNumAtoms()], axis=-1)

        return a if connected and (degree > 0).all() else None

    def _genX(self, mol, max_length=None):

        max_length = max_length if max_length is not None else mol.GetNumAtoms()

        return np.array([self.atom_encoder_m[atom.GetAtomicNum()] for atom in mol.GetAtoms()] + [0] * (
                    max_length - mol.GetNumAtoms()), dtype=np.int32)

    def _genS(self, mol, max_length=None):

        max_length = max_length if max_length is not None else len(Chem.MolToSmiles(mol))

        return np.array([self.smiles_encoder_m[c] for c in Chem.MolToSmiles(mol)] + [self.smiles_encoder_m['E']] * (
                    max_length - len(Chem.MolToSmiles(mol))), dtype=np.int32)

    def _genF(self, mol, max_length=None):

        max_length = max_length if max_length is not None else mol.GetNumAtoms()

        features = np.array([[*[a.GetDegree() == i for i in range(5)],
                              *[a.GetExplicitValence() == i for i in range(9)],
                              *[int(a.GetHybridization()) == i for i in range(1, 7)],
                              *[a.GetImplicitValence() == i for i in range(9)],
                              a.GetIsAromatic(),
                              a.GetNoImplicit(),
                              a.GetFormalCharge(),
                              *[a.GetNumExplicitHs() == i for i in range(5)],
                              *[a.GetNumImplicitHs() == i for i in range(5)],
                              *[a.GetNumRadicalElectrons() == i for i in range(5)],
                              a.IsInRing(),
                              *[a.IsInRingSize(i) for i in range(2, 9)]] for a in mol.GetAtoms()], dtype=np.int32)

        return np.vstack((features, np.zeros((max_length - features.shape[0], features.shape[1]))))

    def match_atoms(self, atoms):
        self.data = [mol for mol in self.data if set(map(lambda a: a.GetSymbol(), mol.GetAtoms())) <= set(atoms)]

    def matrices2mol(self, node_labels, edge_labels, strict=False):
        mol = Chem.RWMol()

        for node_label in node_labels:
            mol.AddAtom(Chem.Atom(self.atom_decoder_m[node_label]))

        for start, end in zip(*np.nonzero(edge_labels)):
            if start > end:
                mol.AddBond(int(start), int(end), self.bond_decoder_m[edge_labels[start, end]])

        if strict:
            try:
                Chem.SanitizeMol(mol)
            except:
                mol = None

        return mol

    def seq2mol(self, seq, strict=False):
        mol = Chem.MolFromSmiles(''.join([self.smiles_decoder_m[e] for e in seq if e != 0]))

        if strict:
            try:
                Chem.SanitizeMol(mol)
            except:
                mol = None

        return mol

    def _generate_train_validation_test(self, validation, test):

        self.log('Creating train, validation and test sets..')

        validation = int(validation * len(self))
        test = int(test * len(self))
        train = len(self) - validation - test

        self.all_idx = np.random.permutation(len(self))
        self.train_idx = self.all_idx[0:train]
        self.validation_idx = self.all_idx[train:train + validation]
        self.test_idx = self.all_idx[train + validation:]

        self.train_counter = 0
        self.validation_counter = 0
        self.test_counter = 0

        self.train_count = train
        self.validation_count = validation
        self.test_count = test

        self.log('Created train ({} items), validation ({} items) and test ({} items) sets!'.format(
            train, validation, test))

    def _next_batch(self, counter, count, idx, batch_size):
        if batch_size is not None:
            if counter + batch_size >= count:
                counter = 0
                np.random.shuffle(idx)

            output = [obj[idx[counter:counter + batch_size]]
                      for obj in (self.data, self.smiles, self.data_S, self.data_A, self.data_X,
                                  self.data_D, self.data_F, self.data_Le, self.data_Lv)]

            counter += batch_size
        else:
            output = [obj[idx] for obj in (self.data, self.smiles, self.data_S, self.data_A, self.data_X,
                                           self.data_D, self.data_F, self.data_Le, self.data_Lv)]

        return [counter] + output

    def next_train_batch(self, batch_size=None):
        out = self._next_batch(counter=self.train_counter, count=self.train_count,
                               idx=self.train_idx, batch_size=batch_size)
        self.train_counter = out[0]

        return out[1:]

    def next_validation_batch(self, batch_size=None):
        out = self._next_batch(counter=self.validation_counter, count=self.validation_count,
                               idx=self.validation_idx, batch_size=batch_size)
        self.validation_counter = out[0]

        return out[1:]

    def next_test_batch(self, batch_size=None):
        out = self._next_batch(counter=self.test_counter, count=self.test_count,
                               idx=self.test_idx, batch_size=batch_size)
        self.test_counter = out[0]

        return out[1:]

    @staticmethod
    def log(msg='', date=True):
        print(str(datetime.now().strftime('%Y-%m-%d %H:%M:%S')) + ' ' + str(msg) if date else str(msg))

    def __len__(self):
        return self.__len


if __name__ == '__main__':
    data = SparseMolecularDataset()
    data.generate('data/chembl.smi', validation=0.01, test=0.01, heavyatom=30,
                  atoms=['C', 'N', 'O', 'S', 'H', 'F', 'Cl'])
    data.save('data/chembl.sparsedataset')
