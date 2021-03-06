from __future__ import division
import libtbx.load_env
from cctbx import sgtbx
import os

def run():
  """ List seminvariants whose continuous shifts aren't principal,
      drawing from the spacegroup settings specified in phenix_regression
  """
  namespace = {}
  execfile(os.path.join(libtbx.env.find_in_repositories("phenix_regression"),
                        "settings.py"),
           namespace)
  for setting in namespace['settings']:
    sgi = sgtbx.space_group_info(setting)
    seminvar = sgtbx.structure_seminvariants(sgi.group())
    if seminvar.continuous_shifts_are_principal(): continue
    print str(sgi)
    for vm in seminvar.vectors_and_moduli():
      if vm.m != 0: continue
      print "\t", vm.v

if __name__ == '__main__':
  run()
