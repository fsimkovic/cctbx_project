#! /usr/local_cci/Python-2.2.1/bin/python

# Euclidean model matching.

PATH_cctbx_lib_python = "/net/ringneck/scratch1/rwgk/py221/lib_python"
PATH_cctbx            = "/net/cci/rwgk/cctbx"

import sys
sys.stderr = sys.stdout

print "Content-type: text/plain"
print

import traceback
import exceptions
class FormatError(exceptions.Exception): pass

import cgi

sys.path.insert(0, PATH_cctbx_lib_python)
sys.path.insert(0, PATH_cctbx)
from cctbx_boost import sgtbx
from cctbx_boost import uctbx
from cctbx import xutils
from cctbx import euclidean_model_matching as emma

print "sgtbx version:", sgtbx.__version__
print "uctbx version:", uctbx.__version__
print

class Empty: pass

def GetFormData():
  form = cgi.FieldStorage()
  inp = Empty()
  for key in (("ucparams_1", ""),
              ("sgsymbol_1", "P1"),
              ("convention_1", ""),
              ("coor_type_1", None),
              ("skip_columns_1", 0),
              ("ucparams_2", ""),
              ("sgsymbol_2", ""),
              ("convention_2", ""),
              ("coor_type_2", None),
              ("skip_columns_2", 0),
              ("tolerance", "1.0"),
              ("diffraction_index_equivalent", None)):
    if (form.has_key(key[0])):
      inp.__dict__[key[0]] = form[key[0]].value.strip()
    else:
      inp.__dict__[key[0]] = key[1]
  inp.coordinates = []
  for i in xrange(2):
    key = "coordinates_" + str(i+1)
    coordinates = []
    if (form.has_key(key)):
      lines = form[key].value.split("\015\012")
      for l in lines:
        s = l.strip()
        if (len(s) != 0): coordinates.append(s)
    inp.coordinates.append(coordinates)
  return inp

def interpret_coordinate_line(line, skip_columns):
  flds = line.split()
  try: coor = [float(x) for x in flds[skip_columns: skip_columns+3]]
  except: raise FormatError, line
  return " ".join(flds[:skip_columns]), coor

class web_to_model(emma.model):

  def __init__(self, ucparams, sgsymbol, convention,
               coor_type, skip_columns, coordinates):
    xsym = xutils.crystal_symmetry(
      uctbx.UnitCell([float(p) for p in ucparams.split()]),
      sgtbx.SpaceGroup(sgtbx.SpaceGroupSymbols(sgsymbol, convention)).Info(),
      auto_check=1)
    skip_columns = int(skip_columns)
    positions = []
    for line in coordinates:
      label, coor = interpret_coordinate_line(line, skip_columns)
      if (label == ""): label = "Site" + str(len(positions)+1)
      if (coor_type != "Fractional"):
        coor = xsym.UnitCell.fractionalize(coor)
      positions.append(emma.labelled_position(label, coor))
    emma.model.__init__(self, xsym, positions)

if (__name__ == "__main__"):

  inp = GetFormData()

  if (inp.ucparams_2 == ""):
      inp.ucparams_2 = inp.ucparams_1
  if (inp.sgsymbol_2 == ""):
      inp.sgsymbol_2 = inp.sgsymbol_1
      inp.convention_2 = inp.convention_1

  try:
    model1 = web_to_model(
      inp.ucparams_1,
      inp.sgsymbol_1, inp.convention_1,
      inp.coor_type_1, inp.skip_columns_1, inp.coordinates[0])
    model1.show("Model 1")

    model2 = web_to_model(
      inp.ucparams_2,
      inp.sgsymbol_2, inp.convention_2,
      inp.coor_type_2, inp.skip_columns_2, inp.coordinates[1])
    model2.show("Model 2")

    tolerance = float(inp.tolerance)
    print "Tolerance:", tolerance
    if (tolerance <= 0.):
      raise ValueError, "Tolerance must be greater than zero."
    print

    diffraction_index_equivalent = int(inp.diffraction_index_equivalent)
    if (diffraction_index_equivalent):
      print "Models are diffraction index equivalent."
      print

    refined_matches = emma.match_models(model1, model2)
    for match in refined_matches:
      match.show()

  except RuntimeError, e:
    print e
  except:
    ei = sys.exc_info()
    print traceback.format_exception_only(ei[0], ei[1])[0]
