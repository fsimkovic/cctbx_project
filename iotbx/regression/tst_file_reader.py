from __future__ import division
import os
import sys
import libtbx.load_env
from libtbx.utils import Sorry
from libtbx import easy_run
from libtbx.test_utils import Exception_expected
from iotbx import file_reader
from iotbx.file_reader import any_file, sort_by_file_type, group_files
from cctbx import miller
from cctbx import crystal
from cctbx.array_family import flex
import cPickle

if (libtbx.env.has_module("ccp4io")):
  from iotbx import mtz
else:
  mtz = None

def exercise_cif () :
  #--- CIF
  cif_data = """
data_comp_list
loop_
_chem_comp.id
_chem_comp.three_letter_code
_chem_comp.name
_chem_comp.group
_chem_comp.number_atoms_all
_chem_comp.number_atoms_nh
_chem_comp.desc_level
HOH      .   'water                               ' solvent             3   1 .
#
data_comp_HOH
#
loop_
_chem_comp_atom.comp_id
_chem_comp_atom.atom_id
_chem_comp_atom.type_symbol
_chem_comp_atom.type_energy
_chem_comp_atom.partial_charge
 HOH           O      O    OH2      -0.408
 HOH           H1     H    HOH2      0.204
 HOH           H2     H    HOH2      0.204
loop_
_chem_comp_tree.comp_id
_chem_comp_tree.atom_id
_chem_comp_tree.atom_back
_chem_comp_tree.atom_forward
_chem_comp_tree.connect_type
 HOH      O      n/a    .      END
 HOH      H1     O      .      .
 HOH      H2     O      .      .
loop_
_chem_comp_bond.comp_id
_chem_comp_bond.atom_id_1
_chem_comp_bond.atom_id_2
_chem_comp_bond.type
_chem_comp_bond.value_dist
_chem_comp_bond.value_dist_esd
 HOH      O      H1        coval       0.980    0.020
 HOH      O      H2        coval       0.980    0.020
loop_
_chem_comp_angle.comp_id
_chem_comp_angle.atom_id_1
_chem_comp_angle.atom_id_2
_chem_comp_angle.atom_id_3
_chem_comp_angle.value_angle
_chem_comp_angle.value_angle_esd
 HOH      H1     O      H2      106.800    3.000
"""

  f = open("tmp1.cif", "w")
  f.write(cif_data)
  f.close()
  cif = any_file("tmp1.cif")
  cif.assert_file_type("cif")
  # test command-line tool
  lines = easy_run.fully_buffered("iotbx.file_reader tmp1.cif").stdout_lines
  assert ("cif" in lines[0])
  os.remove("tmp1.cif")

def exercise_pdb () :
  pdb_data = """
HEADER    HYDROLASE(METALLOPROTEINASE)            17-NOV-93   1THL
ATOM      1  N   ILE     1       9.581  51.813  -0.720  1.00 31.90      1THL 158
ATOM      2  CA  ILE     1       8.335  52.235  -0.041  1.00 52.95      1THL 159
ATOM      3  C   ILE     1       7.959  53.741   0.036  1.00 26.88      1THL 160
END
"""

  f = open("tmp1.pdb", "w")
  f.write(pdb_data)
  f.close()
  pdb = any_file("tmp1.pdb")
  try :
    pdb.assert_file_type("txt")
  except Sorry :
    pass
  else :
    raise Exception_expected(
      "Expected exception for 'pdb.assert_file_type(\"txt\")'.")
  pdb.assert_file_type("pdb")
  pdb.check_file_type("pdb")
  try :
    pdb.check_file_type("hkl")
  except Sorry :
    pass
  else :
    raise Exception_expected
  try :
    pdb.check_file_type(multiple_formats=["hkl","seq"])
  except Sorry :
    pass
  else :
    raise Exception_expected
  pdb.check_file_type(multiple_formats=["hkl","pdb"])
  os.remove("tmp1.pdb")
  f = open("tmp1.ent.txt", "w")
  f.write(pdb_data)
  f.close()
  pdb = any_file("tmp1.ent.txt")
  pdb.assert_file_type("pdb")
  pdb_data_bad = """
HEADER    HYDROLASE(METALLOPROTEINASE)            17-NOV-93   1THL
ATOM      1  N   ILE     1       9.581  51.813  -0.720  1.00 31.90      1THL 158
ATOM      2  CA  ILE     1       8.335  52.235  -0.041  1.00 52.95      1THL 159
ATOM      3  C   ILE     1       7.959  53.741   0.036  1,00 26.88      1THL 160
END"""
  f = open("tmp1_bad.pdb", "w")
  f.write(pdb_data_bad)
  f.close()
  try :
    pdb = any_file("tmp1_bad.pdb", raise_sorry_if_not_expected_format=True)
  except Sorry :
    pass
  else :
    raise Exception_expected

def exercise_phil () :
  phil_data = """\
refinement {
  input {
    include scope mmtbx.utils.neutron_data_str
    include scope mmtbx.utils.xray_data_str
  }
  refine {
    strategy = *individual_adp *individual_sites *tls occupancies group_adp
      .type = choice(multi=True)
    adp {
      tls = None
        .type = str
        .multiple = True
    }
  }
  main {
    number_of_macro_cycles = 3
      .type = int
    simulated_annealing = True
      .type = bool
  }
}"""

  f = open("tmp1.phil", "w")
  f.write(phil_data)
  f.close()
  phil = any_file("tmp1.phil")
  assert phil.file_type == "phil"
  params = phil.file_object.extract()
  assert params.refinement.main.number_of_macro_cycles == 3
  assert params.refinement.input.xray_data.r_free_flags.test_flag_value == None
  os.remove("tmp1.phil")

def exercise_pickle () :
  f = open("tmp1.pkl", "wb")
  e = OSError(2001, "Hello, world!", "tmp1.phil")
  cPickle.dump(e, f)
  f.close()
  pkl = any_file("tmp1.pkl")
  assert pkl.file_type == "pkl"
  exception = pkl.file_object
  assert (exception.errno == 2001)
  os.remove("tmp1.pkl")

def exercise_sequence () :
  seqs = ["AAKDVKFGVNVLADAV",
          "AAKDVKFGNDARVKML",
          "AVKDVRYGNEARVKIL"]
  headers = ["anb.pdb chain A",
             "anb.pdb chain B",
             "anb.pdb chain C"]
  f = open("sequence.pir", "w")
  f.write("""\
> %s

%s
*""" % (headers[0], seqs[0]))
  f.close()
  f = any_file("sequence.pir")
  assert (f.file_type == "seq")
  assert (f.file_object[0].sequence == (seqs[0] + "*"))
  os.remove("sequence.pir")
  f = open("sequence.dat", "w")
  f.write(seqs[0])
  f.close()
  f = any_file("sequence.dat")
  assert (f.file_type == "seq")
  assert (f.file_object[0].sequence == seqs[0])
  os.remove("sequence.dat")
  f = open("sequence.fa", "w")
  f.write("""\
> %s
%s""" % (headers[0], seqs[0]))
  f.close()
  f = any_file("sequence.fa", "w")
  assert (f.file_type == "seq")
  os.remove("sequence.fa")
  f = open("sequences.fa", "w")
  for header, seq in zip(headers, seqs) :
    f.write("""\
> %s
%s
""" % (header, seq))
  f.close()
  f = any_file("sequences.fa")
  assert (f.file_type == "seq")
  for i, seq_object in enumerate(f.file_object) :
    assert (seq_object.sequence == seqs[i])
  os.remove("sequences.fa")

def exercise_alignment () :
  aln1 = """\
>1mru_A
-----------------GSHMTTPSHLSD-----RYELGEILGFGGMSEVHLARDLRLHR
DVAVKVLRADLARDPSFYLRFRREAQNAAALNHPAIVAVYDTGEAETPAGPLPYIVMEYV
DGVTLRDIVHTEGPMTPKRAIEVIADACQALNFSHQNGIIHRDVKPANIMISATNAVKVM
DFGIARAIADSGNSVTQTAAVIGTAQYLSPEQARGDSVDARSDVYSLGCVLYEVLTGEPP
FTGDSPVSVAYQHVREDPIPPSARHEGLSADLDAVVLKALAKNPENRYQTAAEMRADLVR
VHNGEPPEAPKVLTDAERTSLLSSAAGNLSGPR
>2h34_A
MGSSHHHHHHSSGLVPRGSHMDGTAESREGTQFGPYRLRRLVGRGGMGDVYEAEDTVRER
IVALKLMSETLSSDPVFRTRMQREARTAGRLQEPHVVPIHDFGEID---GQL-YVDMRLI
NGVDLAAMLRRQGPLAPPRAVAIVRQIGSALDAAHAAGATHRDVKPENILVSADDFAYLV
DFGIASATTD--EKLTQLGNTVGTLYYMAPERFSESHATYRADIYALTCVLYECLTGSPP
YQGDQ-LSVMGAHINQAIPRPSTVRPGIPVAFDAVIARGMAKNPEDRYVTCGDLSA----
-----AAHAALATADQDRATDILR--------R"""
  open("seqs.aln", "w").write(aln1)
  f = any_file("seqs.aln")
  f.assert_file_type("aln")
  assert (f.file_object.names == ["1mru_A", "2h34_A"])
  aln2 = """\
MUSCLE (3.8) multiple sequence alignment


1mru_A          -----------------GSHMTTPSHLSD-----RYELGEILGFGGMSEVHLARDLRLHR
2h34_A          MGSSHHHHHHSSGLVPRGSHMDGTAESREGTQFGPYRLRRLVGRGGMGDVYEAEDTVRER
                                 ****  .:   :      * *  ::* ***.:*: * *    *

1mru_A          DVAVKVLRADLARDPSFYLRFRREAQNAAALNHPAIVAVYDTGEAETPAGPLPYIVMEYV
2h34_A          IVALKLMSETLSSDPVFRTRMQREARTAGRLQEPHVVPIHDFGEID---GQL-YVDMRLI
                 **:*::   *: ** *  *:.***..*. *: * :*.::* ** :   * * *: *  :"""
  open("seqs.aln", "w").write(aln2)
  f = any_file("seqs.aln")
  f.assert_file_type("aln")
  assert (f.file_object.names == ["1mru_A", "2h34_A"])

def exercise_hhr () :
  hhr_file = libtbx.env.find_in_repositories(
    relative_path="phenix_regression/misc/hewl.hhr",
    test=os.path.isfile)
  if (hhr_file is not None) :
    f = any_file(hhr_file, valid_types=["seq","aln","hhr","txt"])
    assert (f.file_type == "hhr")
    assert (type(f.file_object).__name__ == "hhsearch_parser")

def exercise_xml () :
  xml_file = libtbx.env.find_in_repositories(
    relative_path="phenix_regression/misc/hewl.xml",
    test=os.path.isfile)
  if (xml_file is not None) :
    f = any_file(xml_file, valid_types=["seq","aln","hhr","xml","txt"])
    assert (f.file_type == "xml")
    assert (f.file_object.nodeName == "#document")
    assert (len(f.file_object.childNodes) == 2)
    assert (f.file_object.childNodes[1].nodeName == "BlastOutput")

def exercise_maps () :
  xplor_map = libtbx.env.find_in_repositories(
    relative_path="phenix_regression/misc/cns.map",
    test=os.path.isfile)
  if xplor_map is not None :
    f = any_file(xplor_map)
    assert f.file_type == "xplor_map"
  ccp4_map = libtbx.env.under_dist(
    module_name="iotbx",
    path="ccp4_map/tst_input.map")
  f = any_file(ccp4_map)
  assert f.file_type == "ccp4_map"

def exercise_hkl () :
  #--- HKL
  crystal_symmetry = crystal.symmetry(
    unit_cell=(10,11,12,85,95,100),
    space_group_symbol="P 1")
  miller_set = miller.build_set(
    crystal_symmetry=crystal_symmetry,
    anomalous_flag=False,
    d_min=3)
  input_arrays = [miller_set.array(
    data=flex.random_double(size=miller_set.indices().size()))
      .set_observation_type_xray_amplitude()
        for i in [0,1]]
  mtz_dataset = input_arrays[0].as_mtz_dataset(column_root_label="F0")
  mtz_dataset.mtz_object().write("tmp1.mtz")
  hkl = any_file("tmp1.mtz")
  assert hkl.file_type == "hkl"
  #assert hkl.file_server
  assert hkl.file_server.miller_arrays[0].info().labels == ["F0"]
  os.remove("tmp1.mtz")

def exercise_misc () :
  file_names = ["foo.pdb", "foo.mtz", "bar.pdb", "bar.mtz", "seq.dat"]
  file_types = ["pdb", "hkl", "pdb", "hkl", "seq"]
  for i, file_name in enumerate(file_names) :
    f = open(file_name, "w")
    f.write("1")
    f.close()
    input_file = file_reader.any_file_fast(file_name)
    assert (input_file.file_type == file_types[i])
    if (input_file.file_type == "hkl") :
      assert (input_file.file_object.file_type() == "CCP4 MTZ")
  file_names = sort_by_file_type(file_names, sort_order=["pdb","hkl","seq"])
  assert (file_names == ['foo.pdb','bar.pdb','foo.mtz','bar.mtz','seq.dat'])
  wc = file_reader.get_wildcard_strings(["hkl","pdb","seq"])
  if (sys.platform != "win32") :
    assert (wc == """Reflections file (*.mtz, *.hkl, *.sca, *.cns, *.xplor, *.cv, *.ref, *.fobs)|*.mtz;*.hkl;*.sca;*.cns;*.xplor;*.cv;*.ref;*.fobs|Model file (*.pdb, *.ent)|*.pdb;*.ent|Sequence file (*.fa, *.faa, *.seq, *.pir, *.dat, *.fasta)|*.fa;*.faa;*.seq;*.pir;*.dat;*.fasta|All files (*.*)|*.*""")
  wc = file_reader.get_wildcard_strings([])
  assert (wc == """All files (*.*)|*.*""")

def exercise_groups () :
  files = [
    "/data/user/project1/p1.sca",
    "/data/user/project2/p2.pdb",
    "/data/user/project1/p1.pdb",
    "/data/user/project1/process/mosflm.log",
    "/data/user/project4/refine/current.pdb",
    "/data/user/project3/data.sca",
    "/data/user/project3/model.pdb",
    "/data/user/project2/native.mtz",
    "/data/user/project3/ligands.cif",
    "/data/user/project4/data.mtz",
    "/data/user/project4/refine/ligands.cif",
    #"/data/user/project2/p2_reindex.pdb",
    #"/data/user/project2/p2_reindex.mtz",
    "/data/user/new_data.hkl",
    "/data/user/project5/model.pdb",
    "/data/user/project5/model2.pdb",
    "/data/user/project5/anom.mtz",
  ]
  g = group_files(files)
  assert (g.ambiguous_files == ['/data/user/new_data.hkl',
                                '/data/user/project5/anom.mtz'])
  assert (g.ungrouped_files == [])
  assert (g.grouped_files == [
    ['/data/user/project2/p2.pdb',
      '/data/user/project2/native.mtz'],
    ['/data/user/project1/p1.pdb',
      '/data/user/project1/p1.sca',
      '/data/user/project1/process/mosflm.log'],
    ['/data/user/project4/refine/current.pdb',
      '/data/user/project4/data.mtz',
      '/data/user/project4/refine/ligands.cif'],
    ['/data/user/project3/model.pdb',
      '/data/user/project3/data.sca',
      '/data/user/project3/ligands.cif'],
    ['/data/user/project5/model.pdb'],
    ['/data/user/project5/model2.pdb'],
  ])
  g = group_files(files, template_format="hkl")
  assert (g.ambiguous_files == [])
  assert (g.ungrouped_files == [])
  assert (g.grouped_files == [
    ['/data/user/project1/p1.sca',
      '/data/user/project1/p1.pdb',
      '/data/user/project1/process/mosflm.log'],
    ['/data/user/project3/data.sca',
      '/data/user/project3/model.pdb',
      '/data/user/project3/ligands.cif'],
    ['/data/user/project2/native.mtz',
      '/data/user/project2/p2.pdb'],
    ['/data/user/project4/data.mtz',
      '/data/user/project4/refine/current.pdb',
      '/data/user/project4/refine/ligands.cif'],
    ['/data/user/new_data.hkl'],
    ['/data/user/project5/anom.mtz',
      '/data/user/project5/model.pdb',
      '/data/user/project5/model2.pdb']
  ])

def exercise () :
  exercise_groups()
  exercise_misc()
  exercise_cif()
  exercise_pdb()
  exercise_phil()
  exercise_pickle()
  exercise_sequence()
  exercise_alignment()
  exercise_hhr()
  exercise_xml()
  exercise_maps()
  if mtz is None :
    print "Skipping mtz file tests"
  else :
    exercise_hkl()
  print "OK"

if __name__ == "__main__" :
  exercise()
#---end
