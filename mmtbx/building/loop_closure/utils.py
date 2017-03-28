from __future__ import division

from mmtbx.conformation_dependent_library import generate_protein_threes
from scitbx.matrix import rotate_point_around_axis
from mmtbx.validation import ramalyze
import math
from cStringIO import StringIO
from mmtbx.validation.ramalyze import res_types
from scitbx.math import dihedral_angle
from libtbx.test_utils import approx_equal
# from scitbx.matrix import _dihedral_angle # python implementation, but on flex arrays

import boost.python
ext = boost.python.import_ext("mmtbx_validation_ramachandran_ext")
from mmtbx_validation_ramachandran_ext import rama_eval

def get_phi_psi_atoms(hierarchy):
  phi_psi_atoms = []
  for three in generate_protein_threes(
        hierarchy=hierarchy,
        geometry=None):
    psatoms = three.get_phi_psi_atoms()
    if psatoms is not None:
      phi_atoms, psi_atoms = psatoms
    else:
      phi_atoms, psi_atoms = None, None
    rama_key = three.get_ramalyze_key()
    # print "rama_key", rama_key
    phi_psi_atoms.append(([phi_atoms, psi_atoms],rama_key))
  return phi_psi_atoms

def py_dihedral_angle2(sites, deg=True):
  def dot_product(a,b):
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
  def cross_product(a,b):
    return (a[1] * b[2] - b[1] * a[2],
            a[2] * b[0] - b[2] * a[0],
            a[0] * b[1] - b[0] * a[1])
  """ sites = [(vec3),(vec3),(vec3),(vec3)]
  # supposed to be fast dihedral calculation, taken from here:
  # http://stackoverflow.com/a/34245697
  # Pure python
  Praxeolitic formula
  1 sqrt, 1 cross product
  2.5 times slower than dihedral_angle in cpp
  """

  p0 = sites[0]
  p1 = sites[1]
  p2 = sites[2]
  p3 = sites[3]

  b0 = (p0[0]-p1[0], p0[1]-p1[1], p0[2]-p1[2])
  b1 = (p2[0]-p1[0], p2[1]-p1[1], p2[2]-p1[2])
  b2 = (p3[0]-p2[0], p3[1]-p2[1], p3[2]-p2[2])

  # normalize b1 so that it does not influence magnitude of vector
  # rejections that come next
  # b1 /= np.linalg.norm(b1)
  b1_norm = math.sqrt(b1[0]*b1[0]+b1[1]*b1[1]+b1[2]*b1[2])
  b1 = (b1[0]/b1_norm, b1[1]/b1_norm, b1[2]/b1_norm)

  # vector rejections
  # v = projection of b0 onto plane perpendicular to b1
  #   = b0 minus component that aligns with b1
  # w = projection of b2 onto plane perpendicular to b1
  #   = b2 minus component that aligns with b1
  b0_dp_b1 = dot_product(b0, b1)
  b2_dp_b1 = dot_product(b2, b1)
  v = (b0[0]-b0_dp_b1*b1[0],
       b0[1]-b0_dp_b1*b1[1],
       b0[2]-b0_dp_b1*b1[2])
  w = (b2[0]-b2_dp_b1*b1[0],
       b2[1]-b2_dp_b1*b1[1],
       b2[2]-b2_dp_b1*b1[2])
  # angle between v and w in a plane is the torsion angle
  # v and w may not be normalized but that's fine since tan is y/x
  x = dot_product(v, w)
  b1_cross_v = cross_product(b1, v)
  y = dot_product(b1_cross_v, w)
  return math.degrees(math.atan2(y, x))

def get_dihedral_angle(atoms, round_coords=False):
  # round here is to emulate rounding when dumping to pdb, to get more
  # consistent result for rama outliers inside program and when calculating
  # from resulted pdb file.
  if atoms is None:
    return None
  sites = []
  if round_coords:
    for x in atoms:
      sites.append((round(x.xyz[0], 3), round(x.xyz[1], 3), round(x.xyz[2], 3)))
  else:
    sites = [x.xyz for x in atoms]
  return dihedral_angle(
      sites = sites,
      deg=True)

def rama_score_evaluate(resType, value):
  return ramalyze.ramalyze.evalScore(resType, value)

def pair_info(phi_psi_pair):
  return phi_psi_pair[0][2].id_str()

def list_rama_outliers_h(hierarchy, r=None):
  if r is None:
    r = rama_eval()
  phi_psi_atoms = get_phi_psi_atoms(hierarchy)
  outp = list_rama_outliers(phi_psi_atoms, r)
  return outp

def pair_selection(phi_psi_pair, margin=1):
  resnum = phi_psi_pair[0][2].parent().parent().resseq_as_int()
  return "(chain %s and resid %s:%s)" % (phi_psi_pair[0][2].parent().parent().parent().id,
      resnum-margin, resnum+margin)

def rama_score_selection(hierarchy, r=None, score="outlier",margin=1):
  assert score in ["outlier", "allowed"]
  test = ramalyze.RAMALYZE_OUTLIER
  if score == "allowed":
    test = ramalyze.RAMALYZE_ALLOWED
  if r is None:
    r = rama_eval()
  out_sel = []
  phi_psi_atoms = get_phi_psi_atoms(hierarchy)
  for phi_psi_pair, rama_key in phi_psi_atoms:
    rama_score = get_rama_score(phi_psi_pair, r, rama_key)
    if rama_evaluate(phi_psi_pair, r, rama_key) == test:
      out_sel.append(pair_selection(phi_psi_pair, margin))
  out_sel_txt = " or ".join(out_sel)
  return out_sel_txt


def list_rama_outliers(phi_psi_atoms, r):
  result = ""
  # out_sel = []
  for phi_psi_pair, rama_key in phi_psi_atoms:
    rama_score = get_rama_score(phi_psi_pair, r, rama_key)
    if rama_evaluate(phi_psi_pair, r, rama_key) == ramalyze.RAMALYZE_OUTLIER:
      result += "  !!! OUTLIER %s, score=%f\n" % (pair_info(phi_psi_pair), rama_score)
    # print "%s, %s, %s" % (pair_info(phi_psi_pair), get_rama_score(phi_psi_pair, r, rama_key), ramalyze.res_types[rama_key])
      # out_sel.append(pair_selection(phi_psi_pair))
    # print_rama_stats(phi_psi_atoms, r)
  # out_sel.txt = " or ".join(out_sel)
  # print out_sel
  return result


def get_rama_score(phi_psi_pair, r, rama_key, round_coords=False):
  # phi_psi_angles = get_pair_angles(phi_psi_pair, round_coords=round_coords)
  phi_psi_angles = get_pair_angles(phi_psi_pair, round_coords=False)
  if phi_psi_angles[0] is None or phi_psi_angles[1] is None:
    return None
  rama_score = r.get_score(rama_key, phi_psi_angles[0], phi_psi_angles[1])
  if round_coords:
    return rama_score*0.98
  return rama_score

def rama_evaluate(phi_psi_pair, r, rama_key, round_coords=False):
  score = get_rama_score(phi_psi_pair, r, rama_key, round_coords=round_coords)
  if score is None:
    return None
  # print "  score, rama_key", score, rama_key
  return r.evaluate_score(rama_key, score)

def get_pair_angles(phi_psi_pair, round_coords=False):
  phi_psi_angles = [0,0]
  phi_psi_angles[0] = get_dihedral_angle(phi_psi_pair[0], round_coords=round_coords)
  phi_psi_angles[1] = get_dihedral_angle(phi_psi_pair[1], round_coords=round_coords)
  return phi_psi_angles

def print_rama_stats(phi_psi_atoms, r):
  result = StringIO()
  for phi_psi_pair, rama_key in phi_psi_atoms:
    for i, atoms in enumerate(phi_psi_pair):
      for a in atoms:
        print >> result, a.id_str()
    rama_score = get_rama_score(phi_psi_pair, r, rama_key)
    print >> result, "rama score:", get_pair_angles(phi_psi_pair), rama_score,
    print >> result, rama_score_evaluate(rama_key, rama_score), rama_key
    print >> result, "="*20
  print >> result, "*"*80
  r = result.getvalue()
  return r

def get_rmsd(fixed_points, moving_points):
  rmsd = 0
  for fp, mp in zip(fixed_points, moving_points):
    rmsd += fp.distance(mp)**2
  return math.sqrt(rmsd)

def get_rmsd_xyz_fixed(fixed_points, moving_points):
  rmsd = 0
  for fp, mp in zip(fixed_points, moving_points):
    rmsd += mp.distance(fp)**2
  return math.sqrt(rmsd)


def rotate_atoms_around_bond(
    moving_h, atom_axis_point_1, atom_axis_point_2, angle, degrees=True,
    direction_forward=True):
  # changes moving_h
  # print "in rotation, iseqs:", atom_axis_point_1.i_seq, atom_axis_point_2.i_seq
  #
  # find xyz based on i_seqs
  rotate_xyz1 = None
  rotate_xyz2 = None
  if not direction_forward:
    angle = -angle
  atoms = moving_h.atoms()
  for a in atoms:
    if a.i_seq == atom_axis_point_1.i_seq:
      rotate_xyz1 = a.xyz
    elif a.i_seq == atom_axis_point_2.i_seq:
      rotate_xyz2 = a.xyz
  # rotate stuff
  for a in atoms:
    if ((direction_forward and a.i_seq > atom_axis_point_1.i_seq) or
        (not direction_forward and a.i_seq < atom_axis_point_1.i_seq)):
      new_xyz = rotate_point_around_axis(
          axis_point_1=rotate_xyz1,
          axis_point_2=rotate_xyz2,
          point=a.xyz,
          angle=angle,
          deg=degrees)
      # let's round them
      # print "actually setting coordinates:", a.i_seq, a.xyz, "->", new_xyz
      # a.set_xyz((round(new_xyz[0], 3), round(new_xyz[1], 3), round(new_xyz[2], 3)))
      a.set_xyz(new_xyz)

def find_nearest_non_outlier_region(phi_psi_pair, r, rama_key):
  def spiral(N, M):
      x,y = 0,0
      dx, dy = 0, -1
      for dumb in xrange(N*M):
          if abs(x) == abs(y) and [dx,dy] != [1,0] or x>0 and y == 1-x:
              dx, dy = -dy, dx            # corner, change direction
          if abs(x)>N/2 or abs(y)>M/2:    # non-square
              dx, dy = -dy, dx            # change direction
              x, y = -y+dx, x+dy          # jump
          yield x, y
          x, y = x+dx, y+dy
  # ==
  phi_psi_angles = get_pair_angles(phi_psi_pair)
  for dx,dy in spiral(360, 360):
    angles = [phi_psi_angles[0]+dx, phi_psi_angles[1]+dy]
    if (r.evaluate_angles(res_types[rama_key], angles[0], angles[1]) == \
        ramalyze.RAMALYZE_FAVORED):
      return angles
