from __future__ import division
from iotbx.pdb.atom_selection import selection_string_from_selection, convert_wildcards_in_chain_id
from scitbx.array_family import flex
from mmtbx.ncs import ncs_search
import mmtbx.ncs.ncs_utils as nu
from libtbx.utils import Sorry
import libtbx.phil
import iotbx.pdb.hierarchy
from mmtbx.ncs import ncs
from scitbx import matrix
import math
import re
import os
import sys

ncs_search_options = """\
ncs_search
  .short_caption = Search options
  .style = box
{
  enabled = False
    .type = bool
    .help = Use NCS restraints or constraints in refinement (can be \
              determined automatically)
    .short_caption = Use NCS
    .style = noauto bold
  exclude_selection = "element H or element D or water"
    .type = atom_selection
    .help = Atoms selected by this selection will be excluded from the model \
      before search procedures will run.
    .expert_level = 2
  chain_similarity_threshold = 0.85
    .type=float
    .short_caption = Sequence alignment threshold
    .help='''Threshold for similarity between matching chains.
      A smaller value cause more chains to be grouped together and can lower
      the number of common residues'''
    .expert_level = 0
  chain_max_rmsd = 2.
    .type = float
    .short_caption = Max RMSD between matching chains
    .help = '''limit of rms difference between chains to be considered
       as copies'''
    .expert_level = 0
  residue_match_radius = 4.0
    .type = float
    .help = Max allowed distance difference between pairs of matching \
        atoms of two residues
    .expert_level = 0
}
"""

# parameters for manual specification of NCS - ASU mapping
ncs_group_phil_str = '''\
ncs_group
  .multiple = True
  .short_caption = NCS group definition
  .style = auto_align
  .expert_level=0
  {
  reference = None
    .type = str
    .short_caption = Reference selection
    .help = 'Residue selection string for the complete master NCS copy'
    .expert_level=0
  selection = None
    .type = str
    .short_caption = NCS related selection
    .help = 'Residue selection string for each NCS copy location in ASU'
    .multiple = True
    .expert_level=0
  }
'''

ncs_group_master_phil = libtbx.phil.parse(ncs_group_phil_str)

class input(object):
  def __init__(self,
          hierarchy=None,
          crystal_symmetry=None, # if sensible_unit_cell_volume() check is needed
          transform_info=None,
          rotations = None,
          translations = None,
          # XXX warning, ncs_phil_groups can be changed inside...
          ncs_phil_groups = None,
          spec_ncs_groups=None,
          exclude_selection="element H or element D or water",
          chain_max_rmsd=2.0,
          log=None,
          chain_similarity_threshold=0.85,
          residue_match_radius=4.0):
    """
    TODO:
    1. Transfer get_ncs_info_as_spec() to ncs/ncs.py:ncs

    Select method to build ncs_group_object

    order of implementation:
    1) rotations,translations
    2) transform_info
    3) ncs_phil_string
    4) ncs_phil_groups
    5) spec file
    6) mmcif file
    7) iotbx.pdb.hierarchy.input object

    Args:
    -----
      pdb_hierarchy_inp: iotbx.pdb.hierarchy.input
      transform_info: object containing MTRIX or BIOMT transformation info
        iotbx.pdb._mtrix_and_biomt_records_container, obtainable by
        iotbx.pdb.input.process_mtrix_records() function.
      rotations: matrix.sqr 3x3 object
      translations: matrix.col 3x1 object
      ncs_phil_string: Phil parameters
        Phil structure
           ncs_group (multiple)
           {
             reference = ''
             selection = ''   (multiple)
           }
      ncs_phil_groups: a list of ncs_groups_container object, containing
        master NCS selection and a list of NCS copies selection
      spec_ncs_groups: ncs_groups object of class mmtbx.ncs.ncs.ncs
      chain_max_rmsd (float): limit of rms difference between chains to be considered
        as copies
      min_percent (float): Threshold for similarity between chains
        similarity define as:
        (number of matching res) / (number of res in longer chain)
      similarity_threshold (float): min similarity between matching chains
      min_contig_length (int): minimum length of matching chain segments
      exclude_misaligned_residues (bool): check and exclude individual residues
        alignment quality
      match_radius (float): max allow distance difference between pairs of matching
        atoms of two residues
    """
    self.total_asu_length = None
    # iselection maps, each master ncs to its copies position in the asu
    # {'selection string_001':iselection, ... '':}
    self.ncs_to_asu_map = {}
    # iselection maps of each ncs copy to its master ncs
    self.asu_to_ncs_map = {}
    # keys are items in ncs_chain_selection, values are lists of selection str
    self.ncs_to_asu_selection = {}
    self.ncs_copies_chains_names = {}
    self.tr_id_to_selection = {}
    # dictionary of transform names, same keys as ncs_to_asu_map
    self.number_of_ncs_groups = 0
    self.ncs_group_map = {}
    # map transform name (s1,s2,...) to transform object
    self.ncs_transform = {}
    # list of transform to chain assignment
    self.transform_chain_assignment = []
    # map transform to list of master ncs parts in its ncs groups
    self.transform_to_ncs = {}
    # master ncs and non-ncs selection in a string and a flex.bool types
    self.ncs_atom_selection = None
    self.ncs_selection_str = ''
    # iselection of all part in ASU that are not related via NCS operators
    self.non_ncs_region_selection = flex.size_t([])
    # All master ncs atoms selection
    self.all_master_ncs_selections = flex.size_t([])
    # list of selection strings of master NCS
    self.ncs_chain_selection = []
    # unique chains or selection identifiers
    self.model_unique_chains_ids = tuple()
    self.selection_ids = set()
    # transform application order
    self.model_order_chain_ids = []
    self.transform_to_be_used = set()
    # order of transforms  - used when concatenating or separating them
    self.transform_order = []
    # keep hierarchy for writing (To have a source of atoms labels)
    self.hierarchy = None
    # residues common to NCS copies. Used for .spec representation
    self.common_res_dict = {}
    # flag indicating if ncs operation found
    self.found_ncs_transforms = False
    # Collect messages, recommendation and errors
    self.messages = ''
    self.old_i_seqs = None
    self.exclude_selection = None
    self.original_hierarchy = None
    self.truncated_hierarchy = None

    extension = ''
    # set search parameters
    self.exclude_selection = exclude_selection
    self.chain_max_rmsd = chain_max_rmsd
    self.residue_match_radius = residue_match_radius
    self.chain_similarity_threshold = chain_similarity_threshold
    #
    if log is None:
      self.log = sys.stdout
    else:
      self.log = log

    self.crystal_symmetry=crystal_symmetry

    if hierarchy:
      # for a in hierarchy.atoms():
      #   print "oo", a.i_seq, a.id_str()
      # print "====="
      hierarchy.reset_i_seq_if_necessary()
      self.original_hierarchy = hierarchy.deep_copy()
      self.original_hierarchy.reset_atom_i_seqs()
      if self.exclude_selection is not None:
        # pdb_hierarchy_inp.hierarchy.write_pdb_file("in_ncs_pre_before.pdb")
        cache = hierarchy.atom_selection_cache()
        sel = cache.selection("not (%s)" % self.exclude_selection)
        self.truncated_hierarchy = hierarchy.select(sel)
      else:
        # this could be to save iseqs but I'm not sure
        self.truncated_hierarchy = hierarchy.select(flex.size_t_range(hierarchy.atoms_size()))
      self.old_i_seqs = self.truncated_hierarchy.atoms().extract_i_seq()
      # print "self.old_i_seqs", list(self.old_i_seqs)
      # self.truncated_hierarchy.atoms().reset_i_seq()
      self.truncated_hierarchy.reset_atom_i_seqs()
      # self.truncated_hierarchy.write_pdb_file("in_ncs_pre_after.pdb")

      if self.truncated_hierarchy.atoms_size() == 0:
        self.total_asu_length = 0
        return

    #
    # print "ncs_groups before validation", ncs_phil_groups
    validated_ncs_phil_groups = None
    validated_ncs_phil_groups = self.validate_ncs_phil_groups(
      pdb_h = self.truncated_hierarchy,
      ncs_phil_groups   = ncs_phil_groups)
    # print "ncs_phil_groups", ncs_phil_groups
    # print "validated_ncs_phil_groups", validated_ncs_phil_groups
    transform_info = insure_identity_is_in_transform_info(transform_info)
    if transform_info or rotations:
      if ncs_only(transform_info) or rotations:
        if not sensible_unit_cell_volume(
                pdb_h=self.truncated_hierarchy,
                crystal_symmetry=self.crystal_symmetry):
          raise Sorry('Unit cell is to small to contain all NCS copies')
        self.build_ncs_obj_from_pdb_ncs(
          pdb_h = self.truncated_hierarchy,
          rotations=rotations,
          translations=translations,
          transform_info=transform_info)
      else:
        # in the case that all ncs copies are in pdb
        self.build_ncs_obj_from_pdb_asu(pdb_h=self.truncated_hierarchy)
    elif validated_ncs_phil_groups:
      self.build_ncs_obj_from_phil(
        ncs_phil_groups=validated_ncs_phil_groups,
        pdb_h= self.truncated_hierarchy)
    elif spec_ncs_groups:
      self.build_ncs_obj_from_spec_file(
        pdb_h= self.truncated_hierarchy,
        spec_ncs_groups=spec_ncs_groups)
    elif (self.truncated_hierarchy
        and validated_ncs_phil_groups is None):
      # print "Last chance, building from hierarchy"
      self.build_ncs_obj_from_pdb_asu(pdb_h=self.truncated_hierarchy)
    else:
      pass
      # raise Sorry('Please provide one of the supported input')



    # error handling
    self.found_ncs_transforms = (len(self.transform_to_be_used) > 0)
    if self.found_ncs_transforms == 0:
      print >> self.log,'========== WARNING! ============\n'
      print >> self.log,'  No NCS relation were found !!!\n'
      print >> self.log,'================================\n'
    if self.messages != '':
      print >> self.log, self.messages

  def pdb_h_into_chain(self, pdb_h, ch_id="A"):

    new_chain = iotbx.pdb.hierarchy.chain(id=ch_id)
    # print "iseqs in pdb_h_into_chain", list(pdb_h.atoms().extract_i_seq())
    n_res_groups = 0
    for chain in pdb_h.only_model().chains():
      n_res_groups += chain.residue_groups_size()
    new_chain.pre_allocate_residue_groups(
        number_of_additional_residue_groups=n_res_groups)
    new_resseq = 1
    for chain in pdb_h.only_model().chains():
      for rg in chain.residue_groups():
        new_rg = rg.detached_copy()
        new_rg.resseq = new_resseq
        original_iseqs = rg.atoms().extract_i_seq()
        for atom, orig_iseq in zip(new_rg.atoms(), original_iseqs):
          atom.tmp = orig_iseq
        new_resseq += 1
        new_chain.append_residue_group(residue_group=new_rg)
    # print "iseqs2 in pdb_h_into_chain", list(new_chain.atoms().extract_i_seq())
    # print "tmp2 in pdb_h_into_chain", list(new_chain.atoms().extract_tmp_as_size_t())
    return new_chain

  def get_next_ch_id(self, cur_ch_id):
    if len(cur_ch_id) == 1 and cur_ch_id < 'Z':
      return chr(ord(cur_ch_id) + 1)
    elif len(cur_ch_id) == 1 and cur_ch_id == 'Z':
      return "AA"
    elif len(cur_ch_id) == 2 and cur_ch_id[1] < 'Z':
      return cur_ch_id[0] + chr(ord(cur_ch_id[1]) + 1)
    elif len(cur_ch_id) == 2 and cur_ch_id[1] == 'Z':
      return chr(ord(cur_ch_id[0]) + 1) + 'A'


  def validate_ncs_phil_groups(self, pdb_h, ncs_phil_groups):
    """
    Note that the result of this procedure is corrected ncs_phil_groups.
    These groups will be later submitted to build_ncs_obj_from_phil
    procedure. This is sub-optimal and should be changed because
    everything is already processed here and ready to build proper
    NCS_restraint_group object.
    """
    def show_particular_ncs_group(ncs_gr):
      p_obj = ncs_group_master_phil.extract()
      p_obj.ncs_group[0].reference = ncs_gr.reference
      p_obj.ncs_group[0].selection = ncs_gr.selection
      to_show = ncs_group_master_phil.format(python_object=p_obj)
      to_show.show(out=self.log)

    def show_empty_selection_error_message(ng, where="reference"):
      print >> self.log, "  Missing or corrupted %s field:" % where
      print >> self.log, "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      print >> self.log, "      _ALL_ user-supplied groups will be ignored"
      print >> self.log, "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
      show_particular_ncs_group(ng)

    # Massage NCS groups
    # return ncs_phil_groups
    validated_ncs_groups = []
    if ncs_phil_groups is None:
      return None
    if(ncs_phil_groups is not None and len(ncs_phil_groups)==0):
      # print "exiting here"
      ncs_phil_groups=None
      return None
    if (ncs_phil_groups is not None and
        len(ncs_phil_groups)==1 and
        ncs_phil_groups[0].reference is None and
        len(ncs_phil_groups[0].selection) == 1 and
        ncs_phil_groups[0].selection[0] is None):
      # This is empty ncs_group definition somehow creeped into here.
      # Not a big deal.
      return None
    if(ncs_phil_groups is not None):
      print >> self.log, "Validating user-supplied NCS groups..."
      empty_cntr = 0
      for ng in ncs_phil_groups:
        if ng.reference is None or len(ng.reference.strip())==0:
          show_empty_selection_error_message(ng, where="reference")
          empty_cntr += 1
        for s in ng.selection:
          if s is None or len(s.strip())==0:
            show_empty_selection_error_message(ng, where="selection")
            empty_cntr += 1
      if(empty_cntr>0):
        print >> self.log, "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        print >> self.log, "      _ALL_ user-supplied groups are ignored."
        print >> self.log, "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        ncs_phil_groups=None
        return None
    # Verify NCS selections
    # print "ncs_phil_groups", ncs_phil_groups
    if pdb_h is None:
      # if we are here and there is no pdb_hierarchy, we just return
      # user-selections.
      return ncs_phil_groups
    if(ncs_phil_groups is not None and len(ncs_phil_groups)>0):
      msg="Empty selection in NCS group definition: %s"
      asc = pdb_h.atom_selection_cache()
      for ncs_group in ncs_phil_groups:
        print >> self.log, "  Validating:"
        show_particular_ncs_group(ncs_group)
        selection_list = []
        # first, check for selections producing 0 atoms
        user_original_reference_iselection = None
        user_original_copies_iselections = []
        n_atoms_in_user_ncs = 0
        s_string = ncs_group.reference
        if(s_string is not None):
          sel = asc.iselection(s_string)
          selection_list.append(s_string)
          n_reference = sel.size()
          n_atoms_in_user_ncs = n_reference
          if(n_reference==0):
            raise Sorry(msg%s_string)
          user_original_reference_iselection = sel
        for s_string in ncs_group.selection:
          if(s_string is not None):
            sel = asc.iselection(s_string)
            selection_list.append(s_string)
            n_copy = sel.size()
            if(n_copy==0):
              raise Sorry(msg%s_string)
            user_original_copies_iselections.append(sel)
        #
        # The idea for user's groups is to pick them one by one,
        # select only reference and selections from the model,
        # If there are multiple chains in ref or selection -
        # combine them in one chain,
        # save atom original i_seq in atom.tmp
        # run searching procedure for the resulting hierarchy
        # if the user's selections were more or less OK - there should be
        # one group, get atom.tmp values for the selected atoms and using
        # original hierarchy convert them into string selections when needed.
        # If multiple groups produced - use them, most likely the user
        # provided something really wrong.
        # Need to pay some attention to what came out as master and what order
        # of references.
        #
        combined_h = iotbx.pdb.hierarchy.root()
        combined_h.append_model(iotbx.pdb.hierarchy.model())
        cur_ch_id = 'A'
        master_chain = self.pdb_h_into_chain(pdb_h.select(
            user_original_reference_iselection),ch_id=cur_ch_id)
        # print "tmp in master chain:", list(master_chain.atoms().extract_tmp_as_size_t())
        cur_ch_id = self.get_next_ch_id(cur_ch_id)
        combined_h.only_model().append_chain(master_chain)

        # combined_h = iotbx.pdb.hierarchy.new_hierarchy_from_chain(master_chain)
        # print "tmp combined_h1:", list(combined_h.atoms().extract_tmp_as_size_t())
        for uocis in user_original_copies_iselections:
          # print "adding selection to combined:", s_string
          sel_chain = self.pdb_h_into_chain(pdb_h.select(
            uocis),ch_id=cur_ch_id)
          combined_h.only_model().append_chain(sel_chain)
          cur_ch_id = self.get_next_ch_id(cur_ch_id)

        # save old i_seqs in tmp
        # for chain in combined_h.only_model().chains():
        #   for rg in chain.residue_groups():
        #     for ag in rg.atom_groups():
        #       for atom in ag.atoms():
        #         atom.tmp = atom.i_seq
        #         print "  atom.tmp", atom.tmp
        combined_h.reset_atom_i_seqs()
        # combined_h.write_pdb_file("combined_in_validation.pdb")
        # print "tmp:", list(combined_h.atoms().extract_tmp_as_size_t())


        # XXX Here we will regenerate phil selections using the mechanism
        # for finding NCS in this module. Afterwards we should have perfectly
        # good phil selections, and later the object will be created from
        # them.
        # Most likely this is not the best way to validate user selections.

        # selection_list
        chain_info = ncs_search.get_chains_info(
            ph = combined_h,
            selection_list=None)
        # print "chain_info", chain_info
        # Here we want to use relaxed criteria to extract maximum from
        # user's selection
        match_dict = ncs_search.search_ncs_relations(
            ph=combined_h,
            chains_info=chain_info,
            chain_similarity_threshold=min(self.chain_similarity_threshold, 0.5),
            chain_max_rmsd=max(self.chain_max_rmsd, 10.0),
            residue_match_radius=max(self.residue_match_radius, 1000.0),
            )
        group_dict = ncs_search.ncs_grouping_and_group_dict(
            match_dict, combined_h)
        # print "group_dict", group_dict
        # hopefully, we will get only 1 ncs group
        # ncs_group.selection = []
        if len(group_dict) == 0:
          # this means that user's selection doesn't match
          # print "ZERO NCS groups found"
          rejected_msg = "  REJECTED because copies don't match good enough.\n" + \
          "Try to revise selections or adjust chain_similarity_threshold or \n" + \
          "chain_max_rmsd parameters."
          print >> self.log, rejected_msg
          continue
        # User triggered the fail of this assert!
        # print "  N found ncs_groups:", len(group_dict)
        # assert len(group_dict) == 1, "Got %d" % len(group_dict)
        selections_were_modified = False
        for key, ncs_gr in group_dict.iteritems():
          # print "dir ncs_gr:", dir(ncs_gr)
          new_ncs_group = ncs_group_master_phil.extract().ncs_group[0]
          # print "  new reference:", new_ncs_group.reference
          # print "  new selection:", new_ncs_group.selection

          for i, isel in enumerate(ncs_gr.iselections):
            m_all_list = [x for ix in isel for x in list(ix)]
            m_all_list.sort()
            m_all_isel = flex.size_t(m_all_list)
            # print "tmp:", list(combined_h.atoms().extract_tmp_as_size_t())
            original_m_all_isel = combined_h.atoms().\
                select(m_all_isel).extract_tmp_as_size_t()
            if n_atoms_in_user_ncs > original_m_all_isel.size():
              selections_were_modified = True
            # print "new isels", list(m_all_isel)
            # print "old isels", list(original_m_all_isel)
            all_m_select_str = selection_string_from_selection(
                pdb_h=pdb_h,
                selection=original_m_all_isel,
                chains_info=None,
                atom_selection_cache=asc)
            # print "all_m_select_str", all_m_select_str
            if i == 0:
              new_ncs_group.reference=all_m_select_str
            else:
              new_ncs_group.selection.append(all_m_select_str)
          new_ncs_group.selection = new_ncs_group.selection[1:]
          validated_ncs_groups.append(new_ncs_group)
        # Finally, we may check the number of atoms in selections that will
        # go further
        s_string = new_ncs_group.reference
        # print "after validation user's phil:"
        # print "  reference:", s_string
        if(s_string is not None):
          sel = asc.iselection(s_string)
          selection_list.append(s_string)
          n_reference = sel.size()
          if(n_reference==0):
            raise Sorry(msg%s_string)
        for s_string in new_ncs_group.selection:
          # print "  selection:", s_string
          if(s_string is not None):
            sel = asc.iselection(s_string)
            n_copy = sel.size()
            if(n_reference != n_copy):
              raise Sorry(
                "Bad NCS group selections: Natoms(copy)!=Natoms(reference)")
            if(n_copy==0):
              raise Sorry(msg%s_string)
        ok_msg = "  OK. All atoms were included in" +\
        " validated selection.\n"
        modified_msg = "  MODIFIED. Some of the atoms were excluded from" + \
        " your selection.\n  The most common reasons are:\n" + \
        "    1. Missing residues in one or several copies in NCS group.\n" + \
        "    2. Presence of alternative conformations (they are excluded).\n" + \
        "    3. Residue mismatch in requested copies.\n" + \
        "  Please check the validated selection further down.\n"
        if selections_were_modified:
          print >> self.log, modified_msg
        else:
          print >> self.log, ok_msg
    # print "len(validated_ncs_groups)", len(validated_ncs_groups)
    # for ncs_gr in validated_ncs_groups:
    #   print "  reference:", ncs_gr.reference
    #   print "  selection:", ncs_gr.selection
    return validated_ncs_groups

  def build_ncs_obj_from_pdb_ncs(self,
                                 pdb_h,
                                 transform_info=None,
                                 rotations = None,
                                 translations = None):
    """
    Build transforms objects and NCS <-> ASU mapping using PDB file containing
    a single NCS copy and MTRIX  or BIOMT records

    Args:
      pdb_h : iotbx.pdb.hierarchy
      transform_info : an object containing MTRIX or BIOMT transformation info
      rotations : matrix.sqr 3x3 object
      translations : matrix.col 3x1 object
    """
    self.collect_basic_info_from_pdb(pdb_h=pdb_h)
    if bool(transform_info or (rotations and translations)):
      if rotations:
        # add rotations,translations to ncs_refinement_groups
        self.add_transforms_to_ncs_refinement_groups(
          rotations=rotations,
          translations=translations)
      else:
        # use only MTRIX/BIOMT records from PDB
        self.process_pdb(transform_info=transform_info)
      self.transform_chain_assignment = get_transform_order(self.transform_to_ncs)
      self.ncs_copies_chains_names = self.make_chains_names(
        transform_assignment=self.transform_chain_assignment,
        unique_chain_names = self.model_unique_chains_ids)
      # build self.ncs_to_asu_selection
      if self.old_i_seqs is not None:
        len_old_i_seq = self.old_i_seqs.size()
        max_old_i_seq = self.old_i_seqs[-1]
        n = 1
      group_by_tr = {}
      for k in self.transform_chain_assignment:
        if self.old_i_seqs is not None:
          new_part = []
          for a in range(len_old_i_seq):
            kkk = self.old_i_seqs[a]+n*(max_old_i_seq+1)
            new_part.append(self.old_i_seqs[a]+n*(max_old_i_seq+1))
          self.old_i_seqs.extend(flex.size_t(new_part))
          n += 1
        selection_str = 'chain ' + self.ncs_copies_chains_names[k]
        key,tr_id =  k.split('_')
        # build master and copies selections
        self.tr_id_to_selection[k] = (key,selection_str)
        r = self.ncs_transform[tr_id].r
        t = self.ncs_transform[tr_id].t
        if not is_identity(r,t):
          if group_by_tr.has_key(tr_id):
            if not(key in group_by_tr[tr_id][0]):
              group_by_tr[tr_id][0].append(key)
              group_by_tr[tr_id][1].append(selection_str)
          else:
            group_by_tr[tr_id] = [[key],[selection_str]]
      group_key = set([' or '.join(group_by_tr[x][0]) for x in group_by_tr])
      group_val = [' or '.join(group_by_tr[x][1]) for x in group_by_tr]
      assert len(group_key) == 1, group_key
      gk = list(group_key)[0]
      self.ncs_to_asu_selection[gk] = group_val
      # add the identity case to tr_id_to_selection
      for key in  self.ncs_copies_chains_names.iterkeys():
        if not self.tr_id_to_selection.has_key(key):
          sel = key.split('_')[0]
          self.tr_id_to_selection[key] = (sel,sel)
      self.number_of_ncs_groups = 1
      # self.finalize_pre_process(pdb_h=pdb_h)
    else:
      # No NCS transform information
      pass
    self.finalize_pre_process(pdb_h=pdb_h)

  def build_ncs_obj_from_phil(self,
                              ncs_phil_groups = None,
                              pdb_h = None):
    """
    Build transforms objects and NCS <-> ASU mapping using phil selection
    strings and complete ASU

    Args:
      ncs_phil_string : Phil parameters
      ncs_phil_groups :
      pdb_h : iotbx.pdb.hierarchy

    Phil structure
    ncs_group (multiple)
    {
      reference = ''
      selection = ''   (multiple)
    }
    """
    # if(ncs_phil_string is not None):
    #   ncs_phil_string = nu.convert_phil_format(ncs_phil_string)
    # process params
    # if ncs_phil_string:
    #   if isinstance(ncs_phil_string,str):
    #     ncs_phil_string = parse(ncs_phil_string)
    #   phil_param =  master_phil.fetch(
    #     source=ncs_phil_string,track_unused_definitions=True)
    #   working_phil = phil_param[0].extract()
    #   assert  phil_param[1] == [],'Check phil parameters...\n'
    #   ncs_phil_groups = working_phil.ncs_group
    # else:
    assert ncs_phil_groups is not None
    assert self.ncs_selection_str == ''
    unique_selections = set()
    transform_sn = 0
    ncs_group_id = 0
    # populate ncs selection and ncs to copies location
    for group in ncs_phil_groups:
      gns = group.reference
      self.ncs_chain_selection.append(gns)
      unique_selections = uniqueness_test(unique_selections,gns)
      ncs_group_id += 1
      transform_sn += 1
      self.add_identity_transform(
        ncs_selection=gns,
        ncs_group_id=ncs_group_id,
        transform_sn=transform_sn)
      key = format_num_as_str(transform_sn)
      # update with identity transform
      self.update_ncs_copies_chains_names(
            masters = gns,copies = gns, tr_id = key)
      self.update_tr_id_to_selection(gns,gns,key)
      asu_locations = []
      for asu_select in group.selection:
        unique_selections = uniqueness_test(unique_selections,asu_select)
        r, t, rmsd, msg = ncs_search.get_rot_trans(
          ph=pdb_h,
          master_selection=gns,
          copy_selection=asu_select,
          chain_max_rmsd=100)
        # print "rmsd in build_ncs_from_phil", rmsd
        self.messages += msg
        if r.is_zero():
          msg = 'Master NCS and Copy are very poorly related, check selection.'
          self.messages += msg + '\n'
        asu_locations.append(asu_select)
        transform_sn += 1
        key = format_num_as_str(transform_sn)
        self.update_tr_id_to_selection(gns,asu_select,key)
        tr = ncs_search.Transform(
          rotation = r,
          translation = t,
          serial_num = transform_sn,
          coordinates_present = True,
          ncs_group_id = ncs_group_id,
          rmsd=rmsd)
        # Update ncs_group dictionary and transform_to_ncs list
        self.build_transform_dict(
          transform_id = key,
          transform = tr,
          selection_id = gns)
        self.ncs_group_map = update_ncs_group_map(
          ncs_group_map=self.ncs_group_map,
          ncs_group_id = ncs_group_id,
          selection_ids = gns,
          transform_id = key)
        assert not self.ncs_transform.has_key(key)
        self.ncs_transform[key] = tr
        self.selection_ids.add(gns)
        self.update_ncs_copies_chains_names(
            masters = gns,copies = asu_select, tr_id = key)
      self.ncs_to_asu_selection[gns] = asu_locations
      self.number_of_ncs_groups = ncs_group_id

    self.ncs_selection_str = '('+ self.ncs_chain_selection[0] +')'
    for i in range(1,len(self.ncs_chain_selection)):
      self.ncs_selection_str += ' or (' + self.ncs_chain_selection[i] + ')'

    self.transform_chain_assignment = get_transform_order(self.transform_to_ncs)
    self.finalize_pre_process(pdb_h=pdb_h)

  def build_ncs_obj_from_pdb_asu(self,pdb_h):
    """
    Build transforms objects and NCS <-> ASU mapping from a complete ASU
    Note that the MTRIX record are ignored, they are produced in the
    process of identifying the master NCS

    Args::
      pdb_h : pdb_hierarchy
    """
    if len(pdb_h.models()) > 1:
      raise Sorry('Multi-model PDB (with MODEL-ENDMDL) is not supported.')
    chain_ids = {x.id for x in pdb_h.models()[0].chains()}
    self.total_asu_length = pdb_h.atoms_size()
    if len(chain_ids) > 1:
      chains_info = ncs_search.get_chains_info(pdb_h)
      group_dict = ncs_search.find_ncs_in_hierarchy(
        ph=pdb_h,
        chains_info=chains_info,
        chain_similarity_threshold=self.chain_similarity_threshold,
        chain_max_rmsd=self.chain_max_rmsd,
        log=self.log,
        residue_match_radius=self.residue_match_radius)
      # process atom selections
      self.total_asu_length = pdb_h.atoms_size()
      self.build_ncs_obj_from_group_dict(group_dict, pdb_h, chains_info)
      if not self.model_unique_chains_ids:
        model = pdb_h.models()[0]
        chain_ids = {x.id for x in model.chains()}
        self.model_unique_chains_ids = tuple(sorted(chain_ids))

  def build_ncs_obj_from_group_dict(self,group_dict,pdb_h, chains_info=None):
    """
    Use group_dict to build ncs object

    Args:
      pdb_h : pdb_hierarchy
      group_dict (dict):
        keys: tuple of master chain IDs
        values: NCS_groups_container objects with Attributes:
          iselections (list of lists of flex.size_t):
            selection array for the complete ASU
          residue_index_list (list): list of list of matching residues indices
          copies (list of lists):List of lists of the ncs copies chain IDs
          transforms (list of transform objects):
            object with attributes:
              rotation : Rotation matrix object
              translation: Translation matrix object
              serial_num : (int) Transform serial number
              coordinates_present (bool): True when coordinates are presents
              ncs_group_id (int): group ID of the group containing this transform
              rmsd (float): RMS distance between ncs copies
    """
    ph = pdb_h
    asc =  ph.atom_selection_cache()
    # print "in build_ncs_obj_from_group_dict, group_dict"
    # for k,v in group_dict.iteritems():
    #   print "  ", k,
    #   for a in v.iselections:
    #     print "      ", list(a[0])
    #   print "    ", v.residue_index_list
    #   print "    ", v.copies
    #   for z in v.transforms:
    #     print "    ", z.serial_num, z.ncs_group_id, z.coordinates_present
      # print "    ", v.transforms

    self.ncs_atom_selection = flex.bool([False]*self.total_asu_length)
    if chains_info is None:
      chains_info = ncs_search.get_chains_info(ph)
    ncs_related_atoms = flex.bool([False]*self.total_asu_length)
    sorted_group_keys = sorted(group_dict)
    for gr_n,key in enumerate(sorted_group_keys):
      ncs_gr = group_dict[key]
      transform_id = set()
      # get all master selection string
      m_all_list = [x for ix in ncs_gr.iselections[0] for x in list(ix)]
      if len(m_all_list) < 3:
        # we don't want NCS groups with less than 3 atoms.
        continue
      m_all_list.sort()
      m_all_isel = flex.size_t(m_all_list)
      all_m_select_str = selection_string_from_selection(
          ph,
          m_all_isel,
          chains_info=chains_info,
          atom_selection_cache=asc)
      self.ncs_to_asu_selection[all_m_select_str] = []
      #
      for i in xrange(len(ncs_gr.copies)):
        # iterate of ncs copies in group
        tr = ncs_gr.transforms[i]
        tr_id = format_num_as_str(tr.serial_num)
        self.ncs_transform[tr_id] = tr
        for j in xrange(len(ncs_gr.copies[i])):
          # iterate over chains in ncs copy
          m_isel = ncs_gr.iselections[0][j]
          m_ch_id = ncs_gr.copies[0][j]
          m_select_str = selection_string_from_selection(
              ph,
              m_isel,
              chains_info=chains_info,
              atom_selection_cache=asc)
          c_isel = ncs_gr.iselections[i][j]
          c_select_str = selection_string_from_selection(
              ph,
              c_isel,
              chains_info=chains_info,
              atom_selection_cache=asc)
          transform_id.add(tr_id)
          key0 = "chain '{}'_{}".format(
              convert_wildcards_in_chain_id(m_ch_id),tr_id)
          key1 = m_select_str
          key2 = key1 + '_' + tr_id
          self.asu_to_ncs_map[key1] = m_isel.deep_copy()
          self.ncs_to_asu_map[key2] = c_isel.deep_copy()
          self.tr_id_to_selection[key0] = (m_select_str,c_select_str)
          self.selection_ids.add(m_select_str)
          self.update_ncs_copies_chains_names(
            masters = m_select_str,
            copies = c_select_str,
            tr_id = tr_id)
          self.ncs_group_map = update_ncs_group_map(
            ncs_group_map=self.ncs_group_map,
            ncs_group_id = gr_n + 1,
            selection_ids = m_select_str,
            transform_id = tr_id)
          if i == 0:
            # master copy
            master_sel = flex.bool(self.total_asu_length,m_isel)
            self.ncs_atom_selection |=  master_sel
            ncs_related_atoms |=  master_sel
          else:
            # non-identity transforms
            self.transform_to_be_used.add(tr.serial_num)
            # example key: "chain A_s002"
            self.transform_to_ncs = add_to_dict(
              d=self.transform_to_ncs,k=tr_id,v=key2)
            copy_sel = flex.bool(self.total_asu_length,c_isel)
            ncs_related_atoms |=  copy_sel
        # Get complete master and copies selections
        if i != 0:
          c_all_list = [x for ix in ncs_gr.iselections[i] for x in list(ix)]
          c_all_list.sort()
          c_all_isel = flex.size_t(c_all_list)
          c_select_str = selection_string_from_selection(
              ph,
              c_all_isel,
              chains_info=chains_info,
              atom_selection_cache=asc)
          self.ncs_to_asu_selection[all_m_select_str].append(c_select_str)
    #
    self.number_of_ncs_groups = len(group_dict)
    ncs_selection_str_list = []
    selection_ids = sorted(self.selection_ids)
    for sel in selection_ids:
      ncs_selection_str_list.append('(' + sel + ')')
    self.ncs_selection_str = ' or '.join(ncs_selection_str_list)
    self.transform_chain_assignment = get_transform_order(self.transform_to_ncs)

    self.all_master_ncs_selections = self.ncs_atom_selection.iselection(True)
    # add the ncs_atom_selection all the regions that are not NCS related
    self.ncs_atom_selection = self.ncs_atom_selection | (~ncs_related_atoms)
    self.finalize_pre_process(pdb_h=pdb_h)

  def build_ncs_obj_from_spec_file(self,
                                   spec_ncs_groups=None,
                                   pdb_h=None,
                                   join_same_spec_groups = True):
    """
    read .spec files and build transforms object and NCS <-> ASU mapping

    Arguments:
    pdb_h: pdb hierarchy
    spec_ncs_groups: ncs_groups object or mmtbx.ncs.ncs.ncs object
    join_same_spec_groups: (bool) True: combine groups with similar transforms
    """
    if not spec_ncs_groups: spec_ncs_groups = []
    if isinstance(spec_ncs_groups, ncs.ncs):
      spec_ncs_groups = spec_ncs_groups.ncs_groups()
    if spec_ncs_groups:
      transform_sn = 0
      ncs_group_id = 0
      for gr in spec_ncs_groups:
        # create selection
        spec_group_list =get_ncs_group_selection(gr.chain_residue_id())
        gs = spec_group_list[0]
        if join_same_spec_groups:
          # leave groups with the same transforms separate
          group_exist =self.look_and_combine_groups(gr,spec_group_list)
          if group_exist: continue
        ncs_group_id += 1
        self.ncs_chain_selection.append(gs)
        asu_locations = []
        for i,ncs_copy_select in enumerate(spec_group_list):
          # invert transform - the rotation in gr is from the copy to the master
          r = gr.rota_matrices()[i]
          t = gr.translations_orth()[i]
          r,t = inverse_transform(r,t)
          rmsd = round(gr.rmsd_list()[i],4)
          transform_sn += 1
          key = format_num_as_str(transform_sn)
          self.update_tr_id_to_selection(gs,ncs_copy_select,key)
          if not is_identity(r,t):
            asu_locations.append(ncs_copy_select)
          tr = ncs_search.Transform(
            rotation = r,
            translation = t,
            serial_num = transform_sn,
            coordinates_present = True,
            ncs_group_id = ncs_group_id,
            rmsd=rmsd)
          # Update ncs_group dictionary and transform_to_ncs list
          self.build_transform_dict(
            transform_id = key,
            transform = tr,
            selection_id = gs)
          self.ncs_group_map = update_ncs_group_map(
            ncs_group_map=self.ncs_group_map,
            ncs_group_id = ncs_group_id,
            selection_ids = gs,
            transform_id = key)
          assert not self.ncs_transform.has_key(key)
          self.ncs_transform[key] = tr
          self.selection_ids.add(gs)
          self.update_ncs_copies_chains_names(
            masters = gs,copies = ncs_copy_select,tr_id = key)
        self.ncs_to_asu_selection[gs] = asu_locations
      self.number_of_ncs_groups = ncs_group_id

      self.ncs_selection_str = '('+ self.ncs_chain_selection[0] +')'
      for i in range(1,len(self.ncs_chain_selection)):
        self.ncs_selection_str += ' or (' + self.ncs_chain_selection[i] + ')'
      self.transform_chain_assignment=get_transform_order(self.transform_to_ncs)
      self.finalize_pre_process(pdb_h=pdb_h)

  def look_and_combine_groups(self,gr_new,spec_group_list):
    """
    In spec files groups of different masters and copies listed separately,
    even if they have the same rotation/translation and can be combined.
    This function combines them, updates the relevant object attributes and
    returns True/False to indicate if group found

    Args:
      spec_group_list (list): selection string for each ncs copy in the group
      gr_new (object): ncs group object

    Returns:
      found_same_group (bool): indicate if group found
    """
    gs_new = spec_group_list[0]
    found_same_group = False
    gr_r_list = gr_new.rota_matrices()
    gr_t_list = gr_new.translations_orth()
    # in spec files transforms are inverted
    gr_new_list = [inverse_transform(r,t) for (r,t) in zip(gr_r_list,gr_t_list)]
    for k,[_, tr_set] in self.ncs_group_map.iteritems():
      # all transforms need to be the same to join
      if len(gr_r_list) != len(tr_set): continue
      same_transforms = [-1,]*len(tr_set)
      tr_list = list(tr_set)
      tr_list.sort()
      for tr_key1 in tr_list:
        r1 = self.ncs_transform[tr_key1].r
        t1 = self.ncs_transform[tr_key1].t
        for i,(r2,t2) in enumerate(gr_new_list):
          if same_transforms[i] != -1: continue
          same,transpose = ncs_search.is_same_transform(r1,t1,r2,t2)
          test = (same and (not transpose))
          if test:
            same_transforms[i] = i
            break
      found_same_group = (same_transforms.count(-1) == 0)
      # update dictionaries only if same group was found and then break
      if found_same_group:
        self.selection_ids.add(gs_new)
        asu_locations = []
        for i in same_transforms:
          transform_id = tr_list[i]
          ncs_copy_select = spec_group_list[i]
          key = gs_new + '_' + transform_id
          # look at self.ncs_copies_chains_names
          self.update_ncs_copies_chains_names(
            masters=gs_new, copies=ncs_copy_select, tr_id=transform_id)
          r,t = gr_new_list[i]
          self.update_tr_id_to_selection(gs_new,ncs_copy_select,transform_id)
          if not is_identity(r,t):
            self.transform_to_ncs = add_to_dict(
              d=self.transform_to_ncs,k=transform_id,v=key)
            asu_locations.append(ncs_copy_select)

        self.ncs_to_asu_selection[gs_new] = asu_locations
        self.ncs_group_map[k][0].add(gs_new)
        break
    return found_same_group

  def update_ncs_copies_chains_names(self,masters, copies, tr_id):
    masters = get_list_of_chains_selection(masters)
    copies = get_list_of_chains_selection(copies)
    for m,c in zip(masters,copies):
      self.ncs_copies_chains_names[m +'_' + tr_id] = c.replace('chain ','')

  def update_tr_id_to_selection(self,masters, copies,tr_id):
    """
    Args:
      masters: (str) selection of master ncs
      copies: (str) selection of copy
      tr_id: (str) string like "chain A_001" where 001 is the transform number
    """
    tr_keys = get_list_of_chains_selection(masters)
    master_selection_list = separate_selection_string(masters)
    copies_selection_list = separate_selection_string(copies)
    for k,c,m in zip(tr_keys,copies_selection_list,master_selection_list):
      key = k + '_' + tr_id
      self.tr_id_to_selection[key] = (m,c)

  def add_transforms_to_ncs_refinement_groups(self,rotations,translations):
    """
    Add rotation matrices and translations vectors
    to ncs_refinement_groups
    """
    assert len(rotations) == len(translations)
    assert not self.ncs_transform, 'ncs_transform should be empty\n'
    sn = {1}
    self.add_identity_transform(ncs_selection=self.ncs_selection_str)
    n = 1
    for (r,t) in zip(rotations,translations):
      # check if transforms are the identity transform
      if not is_identity(r,t):
        n += 1
        sn.add(n)
        tr_sn = n
      else:
        tr_sn = 1
      key = format_num_as_str(tr_sn)
      tr = ncs_search.Transform(
        rotation = r,
        translation = t,
        serial_num = tr_sn,
        coordinates_present = False,
        ncs_group_id = 1)
      self.ncs_transform[key] = tr
      for select in self.ncs_chain_selection:
        self.build_transform_dict(
          transform_id = key,
          transform = tr,
          selection_id = select)
        self.selection_ids.add(select)
        chain_id = get_list_of_chains_selection(select)
        assert len(chain_id) == 1
        chain_id = chain_id[0]
        self.tr_id_to_selection[chain_id + "'%s'_%s" %(chain_id, key)] = (select,select)
      self.ncs_group_map = update_ncs_group_map(
        ncs_group_map=self.ncs_group_map,
        ncs_group_id = 1,
        selection_ids = self.ncs_chain_selection,
        transform_id = key)

  def collect_basic_info_from_pdb(self,pdb_h):
    """
    Build chain selection string and collect chains IDs from pdb
    Consider that chains can be not continuous
    """
    assert pdb_h is not None
    if len(pdb_h.models()) > 1:
      raise Sorry('Multi-model PDB (with MODEL-ENDMDL) is not supported.')
    model  = pdb_h.models()[0]
    chain_ids = {x.id for x in model.chains()}
    # Collect order if chains IDs and unique IDs
    self.model_unique_chains_ids = tuple(sorted(chain_ids))
    model_order_ch_ids = [(x.id,x.atoms_size()) for x in model.chains()]
    ch_n_atoms = {x:None for x in self.model_unique_chains_ids}
    for (ch,n) in model_order_ch_ids:
      if ch_n_atoms[ch] is None:
        ch_n_atoms[ch] = [(0,n)]
      else:
        _,last_n = ch_n_atoms[ch][-1]
        ch_n_atoms[ch].append((last_n, last_n + n))
    for ch,n in model_order_ch_ids:
      selection_range = ch_n_atoms[ch].pop(0)
      self.model_order_chain_ids.append((ch,selection_range))
    s = ' or chain '.join(self.model_unique_chains_ids)
    self.ncs_selection_str = 'chain ' + s
    assert self.ncs_chain_selection == []
    self.ncs_chain_selection =\
      ['chain ' + s for s in self.model_unique_chains_ids]
    self.ncs_chain_selection.sort()

  def compute_ncs_asu_coordinates_map(self,pdb_h):
    """ Calculates coordinates maps from ncs to asu and from asu to ncs """
    # check is coordinates maps already calculated
    t1 = not bool(self.ncs_atom_selection)
    t2 = not bool(self.asu_to_ncs_map)
    t3 = not bool(self.ncs_to_asu_map)
    if t1 and t2 and t3:
      temp = pdb_h.atom_selection_cache()
      # check if pdb_h contain only the master NCS copy
      pdb_length = len(pdb_h.atoms())
      self.ncs_atom_selection = temp.selection(self.ncs_selection_str)
      ncs_length = self.ncs_atom_selection.count(True)
      # keep track on the asu copy number
      copy_count = {}
      if pdb_length > ncs_length:
        self.total_asu_length = pdb_length
        selection_ref = flex.bool([False]*pdb_length)
        for k in self.transform_chain_assignment:
          key =  k.split('_')[0]
          ncs_selection = temp.selection(key)
          if not self.asu_to_ncs_map.has_key(key):
            copy_count[key] = 0
            selection_ref = (selection_ref | ncs_selection)
            self.asu_to_ncs_map[key] = ncs_selection.iselection(True)
          else:
            copy_count[key] += 1
          # ncs_to_asu_selection is a list of all the copies of a master
          asu_copy_ref = self.ncs_to_asu_selection[key][copy_count[key]]
          asu_selection = temp.selection(asu_copy_ref)
          selection_ref = update_selection_ref(selection_ref,asu_selection)
          self.ncs_to_asu_map[k] = asu_selection.iselection(True)
        self.non_ncs_region_selection = (~selection_ref).iselection(True)
        # add the non ncs regions to the master ncs copy
        self.all_master_ncs_selections = self.ncs_atom_selection.iselection(True)
        self.ncs_atom_selection |= ~selection_ref
        assert set(self.non_ncs_region_selection).intersection(
          set(self.all_master_ncs_selections)) == set(), "%s" % (set(self.non_ncs_region_selection).intersection(
              set(self.all_master_ncs_selections)))
      elif pdb_length == ncs_length:
        # this case is when the pdb hierarchy contain only the master NCS copy
        self.total_asu_length = self.get_asu_length(temp)
        ns = [True]*pdb_length + [False]*(self.total_asu_length - pdb_length)
        self.ncs_atom_selection = flex.bool(ns)
        self.all_master_ncs_selections=self.ncs_atom_selection.iselection(True)
        sorted_keys = sorted(self.transform_to_ncs)
        for i,k in enumerate(sorted_keys):
          v = self.transform_to_ncs[k]
          for transform_key in v:
            key =  transform_key.split('_')[0]
            ncs_selection =flex.bool(self.total_asu_length,temp.iselection(key))
            if not self.asu_to_ncs_map.has_key(key):
              self.asu_to_ncs_map[key] = ncs_selection.iselection(True)
            # make the selection at the proper location at the ASU
            temp_iselection = self.asu_to_ncs_map[key] + ((i + 1) * ncs_length)
            asu_selection = flex.bool(self.total_asu_length,temp_iselection)
            self.ncs_to_asu_map[transform_key] = asu_selection.iselection(True)

  def add_identity_transform(self,ncs_selection,ncs_group_id=1,transform_sn=1):
    """    Add identity transform
    Argument:

    ncs_selection: (str) selection string for the NCS master copy
    ncs_group_id: (int) the NCS group ID
    transform_sn: (int) Over all transform serial number
    """
    transform_obj = ncs_search.Transform(
      rotation = matrix.sqr([1,0,0,0,1,0,0,0,1]),
      translation = matrix.col([0,0,0]),
      serial_num = transform_sn,
      coordinates_present = True,
      ncs_group_id = ncs_group_id)
    id_str = format_num_as_str(transform_sn)
    self.ncs_transform[id_str] = transform_obj
    self.build_transform_dict(
      transform_id = id_str,
      transform = transform_obj,
      selection_id = ncs_selection)
    self.selection_ids.add(ncs_selection)
    self.ncs_group_map = update_ncs_group_map(
      ncs_group_map=self.ncs_group_map,
      ncs_group_id = ncs_group_id,
      selection_ids = ncs_selection,
      transform_id = id_str)

  def process_pdb(self,transform_info):
    """
    Process PDB Hierarchy object
    Args:
      transform_info

    Remarks:
    The information on a chain in a PDB file does not have to be continuous.
    Every time the chain name changes in the pdb file, a new chain is added
    to the model, even if the chain ID already exist. so there model.
    chains() might contain several chains that have the same chain ID
    """
    transform_info_available = bool(transform_info) and bool(transform_info.r)
    if transform_info_available:
      ti = transform_info
      for (r,t,n,cp) in zip(ti.r,ti.t,ti.serial_number,ti.coordinates_present):
        n = int(n)
        key = format_num_as_str(n)
        tr = ncs_search.Transform(
          rotation = r,
          translation = t,
          serial_num = n,
          coordinates_present = cp,
          ncs_group_id = 1)
        for select in self.ncs_chain_selection:
          self.build_transform_dict(
            transform_id = key,
            transform = tr,
            selection_id = select)
          self.selection_ids.add(select)
        self.ncs_group_map = update_ncs_group_map(
          ncs_group_map=self.ncs_group_map,
          ncs_group_id = 1,
          selection_ids = self.ncs_chain_selection,
          transform_id = key)
        # if ncs selection was not provided in phil parameter
        assert not self.ncs_transform.has_key(key)
        self.ncs_transform[key] = tr

  def build_transform_dict(self,
                           transform_id,
                           transform,
                           selection_id):
    """
    Apply all non-identity transforms
    Build transform_to_ncs dictionary, which provides the location of the
    particular chains or selections in the NCS (example key: "002")
    and updates transform_to_be_used set

    Args:
      transform_id (str): 001,002...
      transform : transform object, containing information on transformation
      selection_id (str): NCS selection string
    """
    if not is_identity(transform.r,transform.t):
      self.transform_to_be_used.add(transform.serial_num)
      key = selection_id + '_' + format_num_as_str(transform.serial_num)
      # example key: "chain A_002"
      self.transform_to_ncs = add_to_dict(
        d=self.transform_to_ncs,k=transform_id,v=key)

  def get_asu_length(self,atom_selection_cache):
    """" Collect the length of all ncs copies """
    asu_total_length = self.ncs_atom_selection.count(True)
    for k in self.transform_chain_assignment:
      key =  k.split('_')[0]
      ncs_selection = atom_selection_cache.selection(key)
      asu_total_length += ncs_selection.count(True)
    return asu_total_length

  def build_MTRIX_object(self,ncs_only=True):
    """
    Build a MTRIX object from ncs_group_object
    Used for testing
    """
    assert  self.number_of_ncs_groups < 2
    import iotbx.mtrix_biomt
    result = iotbx.mtrix_biomt.container()
    tr_dict = self.ncs_transform
    tr_sorted = sorted(tr_dict,key=lambda k:tr_dict[k].serial_num)
    for key in tr_sorted:
      transform = self.ncs_transform[key]
      r = transform.r
      t = transform.t
      identity_test = is_identity(r,t)
      cp = (not ncs_only) or identity_test
      result.add(
        r=r,
        t=t,
        coordinates_present=cp,
        serial_number=transform.serial_num)
    return result

  def make_chains_names(self,
                        transform_assignment,
                        unique_chain_names):
    """
    Create a dictionary names for the new NCS copies
    keys: (str) chain_name + '_' + serial_num
    values: (str) (one or two chr long)

    Chain names might repeat themselves several times in a pdb file
    We want copies of chains with the same name to still have the
    same name after similar BIOMT/MTRIX transformation

    Arguments:
    transform_assignment : (list) transformation assignment order
    unique_chain_names : (tuple) a set of unique chain names

    Returns:
    new_names : a dictionary. {'A_1': 'G', 'A_2': 'H',....} map a chain
    name and a transform number to a new chain name
    """
    if not transform_assignment or not unique_chain_names: return {}
    # create list of character from which to assemble the list of names
    # total_chains_number = len(i_transforms)*len(unique_chain_names)
    total_chains_number = len(transform_assignment)
    dictionary_values = nu.make_unique_chain_names(
      unique_chain_names,total_chains_number)
    # create the dictionary
    zippedlists = zip(transform_assignment,dictionary_values)
    new_names_dictionary = {x:y for (x,y) in zippedlists}
    # add the master NCS to dictionary
    tr_set  = {format_num_as_str(x) for x in self.transform_to_be_used}
    for k,v in self.ncs_group_map.iteritems():
      tr_str = (v[1] - tr_set)
      assert len(tr_str) == 1
      tr_str = tr_str.pop()
      for ch_sel in v[0]:
        if not ' or ' in ch_sel:
          new_names_dictionary[ch_sel+'_'+tr_str] = ch_sel.replace('chain ','')
    return new_names_dictionary

  def finalize_pre_process(self,pdb_h=None):
    """
    Steps that are common to most method of transform info
    """
    if pdb_h:
      self.compute_ncs_asu_coordinates_map(pdb_h=pdb_h)
      # print "self.old_i_seqs", list(self.old_i_seqs)
      # print "self.ncs_to_asu_map in finalize"
      # for k, v in self.ncs_to_asu_map.iteritems():
      #   print "  ", k, list(v)
      # print "self.asu_to_ncs_map in finalize"
      # for k, v in self.asu_to_ncs_map.iteritems():
      #   print "  ", k, list(v)
      if self.old_i_seqs is not None:
        for k, v in self.ncs_to_asu_map.iteritems():
          for i in range(len(v)):
            # print v[i], "-->", self.old_i_seqs[v[i]]
            v[i] = self.old_i_seqs[v[i]]
        for k, v in self.asu_to_ncs_map.iteritems():
          for i in range(len(v)):
            # print v[i], "-->", self.old_i_seqs[v[i]]
            v[i] = self.old_i_seqs[v[i]]
      # keep hierarchy for writing
      # self.truncated_hierarchy = pdb_h
      # self.set_common_res_dict()
    # add group selection to ncs_group_map
    for gr_num in self.ncs_group_map.iterkeys():
      gr = self.ncs_group_map[gr_num]
      chains_in_master = chains_in_string(gr[0])
      for sel_str in self.ncs_to_asu_selection.iterkeys():
        chains_in_sel_str = chains_in_string(sel_str)
        if chains_in_master == chains_in_sel_str:
          gr.append(sel_str)
          break
    self.transform_order = sort_dict_keys(self.transform_to_ncs)
    # print "number_of_ncs_groups in finalize", self.number_of_ncs_groups

  def set_common_res_dict(self):
    """
    Build common residues list and related RMSD
    for use when writing spec files list of common residues
    for each chain - transform pair
    """
    # collect all master chain IDs

    sorted_keys = sort_dict_keys(self.ncs_copies_chains_names)
    only_master_ncs_in_hierarchy = False
    if (self.truncated_hierarchy is not None and self.ncs_atom_selection is not None and
        self.ncs_atom_selection.count(True) == self.truncated_hierarchy.atoms_size()):
      only_master_ncs_in_hierarchy = True
    sc = self.truncated_hierarchy.atom_selection_cache()
    #
    for key in sorted_keys:
      master_sel_str, ncs_sel_str = self.tr_id_to_selection[key]
      if only_master_ncs_in_hierarchy:
        # use master ncs for ncs copy residues indices
        copy_selection_indices = sc.iselection(master_sel_str)
        rmsd = 0
      else:
        copy_selection_indices = sc.iselection(ncs_sel_str)
        tr_num = key.split('_')[1]
        tr = self.ncs_transform[tr_num]
        rmsd = tr.rmsd
      # get continuous res ids
      range_list = []
      if len(copy_selection_indices) == 0:
        t_ph = []
      else:
        t_ph = self.truncated_hierarchy.select(copy_selection_indices).models()[0].chains()
      for chain in t_ph:
        res_id = []
        # for rs in chain.residues():
        for rs in chain.residue_groups():
          resid = rs.resid().strip()
          j = rs.resseq_as_int()
          # check if we have insertions
          if str(j) == resid:
            if res_id:
              # step larger than one residue -> close segment
              if (res_id[1] + 1) < j:
                range_list.append(res_id)
                res_id = [j,j]
              else:
                # increase segment range by one
                res_id[1] += 1
            else:
              # start new segment
              res_id = [j,j]
          else:
            # This representation does not handle insertions !!!
            msg = "Sequence may contain insertions and can't be "
            msg += "represented using only residue ID. \n"
            self.messages += msg
        if res_id and (res_id[1] == j):
          # close the last segment
          range_list.append(res_id)
      range_list.sort()
      self.common_res_dict[key] = ([range_list,copy_selection_indices],rmsd)

  def get_ncs_restraints_group_list(self,chain_max_rmsd=10, raise_sorry=True):
    """
    Create a list of ncs_restraint_group objects

    When using phil parameters or badly related copies, consider increasing
    "chain_max_rmsd" value

    Args:
      raise_sorry (bool): When True, raise Sorry if NCS copies don't match

    This should be cached so the work is done only once.
    """
    ncs_restraints_group_list = []
    chain_max_rmsd = max(self.chain_max_rmsd, chain_max_rmsd)
    # assert 0number_of_ncs_groups
    group_id_list = sort_dict_keys(self.ncs_group_map)
    # print "self.ncs_group_map", self.ncs_group_map
    # print "self.asu_to_ncs_map", self.asu_to_ncs_map, list(self.asu_to_ncs_map["chain 'A1'"])
    # print "group_id_list", group_id_list
    # print "self.transform_to_ncs", self.transform_to_ncs
    for k in group_id_list:
      # print "group id", k
      v = self.ncs_group_map[k]
      master_isel = flex.size_t([])
      # Iterate over sorted master selection strings, collect master selection
      # print "sorted v0, v1", sorted(v[0]), sorted(v[1])
      for key in sorted(v[0]):
        if self.asu_to_ncs_map.has_key(key):
          master_isel.extend(self.asu_to_ncs_map[key])
      new_nrg = NCS_restraint_group(flex.sorted(master_isel))
      # print "master isel ", list(master_isel)
      # iterate over transform numbers in the group, collect copies selections
      for tr in sorted(v[1]):
        # print "  tr", tr
        if self.transform_to_ncs.has_key(tr):
          r = self.ncs_transform[tr].r
          t = self.ncs_transform[tr].t
          ncs_isel = flex.size_t([])
          for sel in self.transform_to_ncs[tr]:
            ncs_isel.extend(self.ncs_to_asu_map[sel])
            # print "    ncs_isel", list(ncs_isel)
          ncs_isel = flex.sorted(ncs_isel)
          # print "    ncs_isel", list(ncs_isel)
          new_ncs_copy = NCS_copy(copy_iselection=ncs_isel, rot=r, tran=t)
          new_nrg.copies.append(new_ncs_copy)
      # compare master_isel_test and master_isel
      ncs_restraints_group_list.append(new_nrg)
    # When hierarchy available, test ncs_restraints_group_list
    if self.original_hierarchy and raise_sorry:
      # check that hierarchy is for the complete ASU
      if self.original_hierarchy.atoms_size() == self.total_asu_length:
        import mmtbx.ncs.ncs_utils as nu
        # print "number of atoms in original h", self.original_hierarchy.atoms_size()
        nrgl_ok = nu.check_ncs_group_list(
          ncs_restraints_group_list,
          self.original_hierarchy,
          chain_max_rmsd=chain_max_rmsd,
          log=self.log)
        if not nrgl_ok:
          raise Sorry('NCS copies do not match well')
    return class_ncs_restraints_group_list(ncs_restraints_group_list)

  def update_using_ncs_restraints_group_list(self,ncs_restraints_group_list):
    """
    Update ncs_group_object rotations and transformations.

    Note that to insure proper assignment the ncs_restraints_group_list
    should be produced using the get_ncs_restraints_group_list method

    Args:
      ncs_restraints_group_list: a list of ncs_restraint_group objects
    """
    assert len(ncs_restraints_group_list) == len(self.ncs_group_map)
    group_id_list = sort_dict_keys(self.ncs_group_map)
    for k in group_id_list:
      v = self.ncs_group_map[k]
      nrg = ncs_restraints_group_list.pop(0)
      for tr in sorted(list(v[1])):
        if self.transform_to_ncs.has_key(tr):
          ncs_copy = nrg.copies.pop(0)
          self.ncs_transform[tr].r = ncs_copy.r
          self.ncs_transform[tr].t = ncs_copy.t
          # Test that the correct transforms are updated
          ncs_isel = flex.size_t([])
          for sel in self.transform_to_ncs[tr]:
            ncs_isel.extend(self.ncs_to_asu_map[sel])
          assert ncs_copy.iselection == ncs_isel

  def get_transform_records(self, file_name=None,
                          ncs_only=True,
                          pdb_hierarchy=None,
                          xrs=None,
                          fmodel=None,
                          crystal_symmetry=None,
                          mtrix=None,
                          biomt=None,
                          write=False,
                          log = None):
    """
    Write to a file or prints transformation records.
    with or without PDB atoms and Cryst records.
    If no pdb_hierarchy, xray structure or fmodel are provided, the function
    will return only the MTRIX/BIOMT records

    Args:
      file_name: (str) output file name
      ncs_only: (bool) When False, the comple ASU will be printed (applicable
                only with MTRIX records)
      pdb_hierarchy: (pdb_hierarchy object)
      xrs: (xray structure) for crystal symmetry
      fmodel: (fmodel object)
      crystal_symmetry: crystal symmetry records
      mtrix: (bool) When True -> write MTRIX records
      biomt: (bool) When True -> write BIOMT records
      write: (bool) when False, will will not write to file or print

    Return:
      PDB string
    """
    if not log: log = sys.stdout
    if (not mtrix) and (not biomt):
      mtrix = True
      biomt = False
    assert bool(mtrix) == (not bool(biomt))
    if biomt: ncs_only = True
    mtrix_object = self.build_MTRIX_object(ncs_only=ncs_only)
    pdb_header_str = ''
    new_ph_str = ''
    transform_rec = ''
    #
    if fmodel:
      xrs = fmodel.xray_structure
    if xrs and self.original_hierarchy and (not pdb_hierarchy):
      pdb_hierarchy = self.original_hierarchy
      pdb_str = xrs.as_pdb_file()
      pdb_header_str = get_pdb_header(pdb_str)
      xyz = pdb_hierarchy.atoms().extract_xyz()
      new_xyz = xrs.sites_cart()
      if new_xyz.size() > xyz.size():
        ncs_only = True
        xrs = xrs.select(self.ncs_atom_selection)
        new_xyz = xrs.sites_cart()
      assert new_xyz.size() == xyz.size()
      pdb_hierarchy.atoms().set_xyz(new_xyz)
    if pdb_hierarchy:
      ph = pdb_hierarchy
      if not crystal_symmetry:
        if self.crystal_symmetry: crystal_symmetry = self.crystal_symmetry
        elif xrs:
          crystal_symmetry = xrs.crystal_symmetry()
      pdb_str = ph.as_pdb_string(crystal_symmetry=crystal_symmetry)
      if not pdb_header_str:
       pdb_header_str = get_pdb_header(pdb_str)
      if ncs_only:
        new_ph = ph.select(self.ncs_atom_selection)
      else:
        msg = 'The complete ASU hierarchy need to be provided !!!\n'
        assert len(self.ncs_atom_selection) == len(ph.atoms()),msg
        new_ph = ph
      new_ph_str = new_ph.as_pdb_string(crystal_symmetry=None)
    #
    if mtrix:
      transform_rec = mtrix_object.as_pdb_string()
    elif biomt:
      transform_rec = mtrix_object.format_BOIMT_pdb_string()
    #
    if write:
      if file_name:
        f = open(file_name,'w')
        print >> f, pdb_header_str
        print >> f, transform_rec
        print >> f, new_ph_str
        f.close()
      else:
        print >> log,pdb_header_str
        print >> log,transform_rec
        print >> log,new_ph_str
    return '\n'.join([pdb_header_str,transform_rec,new_ph_str])

  def get_ncs_info_as_spec(
          self,
          pdb_hierarchy_asu=None,
          xrs=None,
          fmodel=None,
          exclude_h=None,
          exclude_d=None,
          stem=None,
          write_ncs_domain_pdb=False,
          log = None):
    """
    This function should be transfered to mmtbx/ncs/ncs.py:ncs class as
    its classmethod, because it creates an object and this is the task of
    a constructor. And it definetely should be decoupled from file creation!


    Returns ncs spec object and can prints ncs info in a ncs_spec,
    format_all_for_resolve or format_all_for_phenix_refine format

    Note that while ncs_groups can master ncs can be comprised from several
    chains, the spec groups can not. So groups with multiple chains in the
    master selection are splitted

    Note that spec format does not support insertions notation
    for example "resseq 49" will include "resid 49" and "resid 49A"

    Args:
      file_name_prefix: (str) output file names prefix
      pdb_hierarchy: (pdb_hierarchy object)
      xrs: (xray structure) for crystal symmetry
      fmodel: (fmodel object)
      write: (bool) when False, will not write to file or print
      exclude_h,exclude_d : parameters of the ncs object
    Return:
      spec_object
    """
    log = log or self.log
    if not stem : stem =''
    else: stem += '_'
    spec_object = ncs.ncs(exclude_h=exclude_h,exclude_d=exclude_d)
    if len(self.common_res_dict) == 0 and self.truncated_hierarchy:
      self.set_common_res_dict()
    if [bool(xrs),bool(pdb_hierarchy_asu),bool(fmodel)].count(True) == 0:
      # if not input containing coordinates is given
      if self.truncated_hierarchy:
        if (self.truncated_hierarchy.atoms_size() == self.total_asu_length):
          xyz = self.truncated_hierarchy.atoms().extract_xyz()
        else:
          # get the ASU coordinates
          nrg = self.get_ncs_restraints_group_list()
          # print "nrg", nrg
          # print self.truncated_hierarchy.as_pdb_string()
          # print "self.total_asu_length", self.total_asu_length
          # print "self.ncs_atom_selection", self.ncs_atom_selection
          xyz = nu.apply_transforms(
            ncs_coordinates = self.truncated_hierarchy.atoms().extract_xyz(),
            ncs_restraints_group_list = nrg,
            total_asu_length =  self.total_asu_length,
            extended_ncs_selection = self.ncs_atom_selection)
    elif fmodel:
      xrs = fmodel.xray_structure
    if xrs and (not pdb_hierarchy_asu):
      xyz = xrs.sites_cart()
    elif pdb_hierarchy_asu:
      xyz = pdb_hierarchy_asu.atoms().extract_xyz()
    # break groups with more than one chain in master
    ncs_groups_by_chains = {}

    # for gr in self.ncs_group_map.itervalues(): old method 2015-05-03 TT
    # run in same order as get_ncs_restraints_group_list()
    group_number = -1
    for k in sort_dict_keys(self.ncs_group_map):
      gr = self.ncs_group_map[k]
      for gr_chains in gr[0]:
        # the same chain can be part of the master NCS in several groups
        if ncs_groups_by_chains.has_key(gr_chains):
          gr_dict = ncs_groups_by_chains[gr_chains]
        else:
          gr_dict = {}
          ncs_groups_by_chains[gr_chains] = gr_dict
        # keep track of copies to avoid multiple identinew_chain_idty matrices
        chains_in_copies = set(gr_dict.values())
        # gr -> [master selection str, set of transforms]
        # Process one chain, in the master ncs, at a time
        for gr_chain in get_list_of_chains_selection(gr_chains):
          for s_str in gr[1]:
            # get ncs copy chain name, that corresponds to master and transform
            gr_key = gr_chain + '_' + s_str
            new_chain_id = self.ncs_copies_chains_names[gr_key]
            if not (new_chain_id in chains_in_copies):
              gr_dict[gr_key] = new_chain_id
              chains_in_copies.add(new_chain_id)
    sorted_group_keys = sorted(ncs_groups_by_chains)
    for gr_dict_key in sorted_group_keys:
      gr_dict = ncs_groups_by_chains[gr_dict_key]
      sorted_keys = sort_dict_keys(gr_dict)
      center_orth = []
      rotations = []
      translations = []
      chain_residue_id_list = []
      chain_residue_range_list = []
      rmsd_list = []
      residues_in_common_list = []
      for k in sorted_keys:
        chain_id = gr_dict[k]
        chain_residue_id_list.append(chain_id)
        [range_list,ncs_sel],rmsd = self.common_res_dict[k]
        chain_residue_range_list.append(range_list)
        center_orth.append(get_center_orth(xyz,ncs_sel))
        transform_key = k.split('_')[1]
        tr = self.ncs_transform[transform_key]
        # use the rmsd of the ncs related atoms rather than the transform
        # rmsd_list.append(tr.rmsd)
        rmsd_list.append(rmsd)
        # in spec files transform is copy -> master, not master -> copy
        r,t = inverse_transform(tr.r,tr.t)
        rotations.append(r)
        translations.append(t)
        res_num = sum([y-x+1 for [x,y] in range_list])
        residues_in_common_list.append(res_num)
      # build group
      group_number += 1
      # XXX This should be consistent with full_file_name parameter in
      # create_ncs_domain_pdb_files()
      # This is here just because we need to output filename of the domain
      # into the spec file if pdb file is going to be created...
      ncs_domain_pdb = None
      if write_ncs_domain_pdb:
        ncs_domain_pdb = stem+'group_'+str(group_number+1)+'.pdb'
      spec_object.import_ncs_group(
        center_orth = center_orth,
        ncs_rota_matr = rotations,
        trans_orth = translations,
        rmsd_list = rmsd_list,
        chain_residue_id = [chain_residue_id_list,chain_residue_range_list],
        residues_in_common_list = residues_in_common_list,
        ncs_domain_pdb = ncs_domain_pdb)
    #
    # There is absolutely no need to put self into spec object.
    #
    spec_object._ncs_obj = self
    return spec_object

  def print_ncs_phil_param(self,write=False,log=None):
    """
    Prints NCS information in the phil parameters format
    lines longer that 80 characters are folded

    Phil structure example:
      ncs_group {
        reference = 'chain A'
        selection = 'chain C'
        selection = 'chain E'
      }
      ncs_group {
        reference = 'chain B'
        selection = 'chain D'
        selection = 'chain F'
      }

    Args:
      write (bool): when true, print to log
      log : location of output, an open file or sys.stdout

    Returns:
      (str): NCS phil parameter string
    """
    if not log: log = sys.stdout
    groups = []
    for master, copies in self.ncs_to_asu_selection.iteritems():
      master = format_80(master)
      groups.append('ncs_group {')
      groups.append("  reference = {}".format(master))
      for cp in copies:
        cp = format_80(cp)
        groups.append("  selection = {}".format(cp))
      groups.append('}')
    gr = '\n'.join(groups)
    gr += '\n'
    if write:
      print >> log,gr
    return gr

  def get_array_of_selections(self):
    """
    Returns array of phil selection strings e.g. for the exapmle above in
    print_ncs_phil_param:
    [['(Chain A)','(chain C)','(chain E)'],['(chain B)','(chain D)','(chain F)']]
    """
    result = []
    for master, copies in self.ncs_to_asu_selection.iteritems():
      group = [master]
      for cp in copies:
        group.append(cp)
      result.append(group)
    return result

  def create_ncs_domain_pdb_files(
          self,
          hierarchy=None,
          exclude_chains=None,
          stem='',
          temp_dir=''):
    """
    Create a PDB file for each NCS group, that contains only the
    group NCS related atoms

    Args:
      hierarchy: PDB hierarchy object
      exclude_chains (list): list of chain IDs to ignore when writing NCS to pdb
      temp_dir (str): temp directory path

    XXX Refactoring ideas:
    1. Make separate function to output one NCS group in a file and use it.
    """
    if not hierarchy: hierarchy = self.original_hierarchy
    if not hierarchy: return None
    if not stem : stem =''
    else: stem += '_'

    if self.number_of_ncs_groups == 0: return None
    nrgl = self.get_ncs_restraints_group_list()
    for group_number in range(len(nrgl)):
      group_isel = nu.ncs_group_iselection(nrgl,group_number)
      # XXX This should be consistent with ncs_domain_pdb parameter in
      # get_ncs_info_as_spec()
      file_name = stem+'group_'+str(group_number+1)+'.pdb'
      full_file_name=os.path.join(temp_dir,file_name)
      ph = hierarchy.select(group_isel)
      if exclude_chains is not None and len(exclude_chains) > 0:
        asc = ph.atom_selection_cache()
        excl_ch_arr = ["not chain '%s'" % x for x in exclude_chains ]
        excl_str = " and ".join(excl_ch_arr)
        sel = asc.selection(excl_str)
        ph = ph.select(sel)
      ph.write_pdb_file(
          file_name=full_file_name,
          crystal_symmetry=self.crystal_symmetry)

  def build_asu_hierarchy(self,
                          pdb_hierarchy,
                          round_coordinates=True):
    """
    Build ASU hierarchy

    Arguments:
    pdb_hierarchy: pdb hierarchy of the master NCS
    round_coordinates: (bool) round coordinates of new NCS copies,
                        for sites_cart constancy
    Return:
    ASU hierarchy
    """
    if len(pdb_hierarchy.models()) > 1:
      raise Sorry('Multi-model PDB (with MODEL-ENDMDL) is not supported.')
    # Build only for PDB when there is a single NCS group
    # print "self.number_of_ncs_groups in build_asu_hierarchy", self.number_of_ncs_groups
    assert self.number_of_ncs_groups < 2
    new_ph = pdb_hierarchy.deep_copy()
    ncs_restraints_group_list = self.get_ncs_restraints_group_list()
    new_sites = nu.apply_transforms(
      ncs_coordinates = pdb_hierarchy.atoms().extract_xyz(),
      ncs_restraints_group_list = ncs_restraints_group_list,
      total_asu_length =  self.total_asu_length,
      extended_ncs_selection = flex.size_t_range(pdb_hierarchy.atoms_size()),
      round_coordinates = round_coordinates)
    model = new_ph.models()[0]
    tr_assignment_order = []
    for tr in self.transform_order:
      for (ch_id, (sel_start,sel_end)) in self.model_order_chain_ids:
        key = 'chain ' + ch_id
        tr_key  =  key + '_' + tr
        ncs_selection = self.asu_to_ncs_map[key][sel_start:sel_end]
        tr_assignment_order.append([tr_key,ncs_selection])
    for tr,ncs_selection in tr_assignment_order:
      new_part = pdb_hierarchy.select(ncs_selection).deep_copy()
      new_chain = iotbx.pdb.hierarchy.ext.chain()
      new_chain.id = self.ncs_copies_chains_names[tr]
      for res in new_part.residue_groups():
        new_chain.append_residue_group(res.detached_copy())
      model.append_chain(new_chain)
    new_ph.atoms().set_xyz(new_sites)
    # print "self.number_of_ncs_groups in build_asu_hierarchy", self.number_of_ncs_groups
    return new_ph

  def show(self,
           format=None,
           verbose=False,
           prefix='',
           header=True,
           log=None):

    """
    Display NCS object

    Args:
      format (str): "phil" : phil file representation
                    "spec" : spec representation out of NCS groups
                    "cctbx": cctbx representation out of NCS groups
                    "restraints"  : .ncs (phenix refine) format
                    "constraints" : .ncs (phenix refine) format
      verbose (bool): when True, will print selection strings, rotation and
        translation info
      prefix (str): a string to be added, padding the output, at the left of
        each line
      header (bool): When True, include header
      log: where to log the output, by default set to sys.stdout
    """
    if not log: log = self.log
    out_str = ''
    # assert 0
    if (not format) or (format.lower() == 'cctbx'):
      out_str = self.__repr__(prefix)
      print >> log, out_str
      if verbose:
        print >> log, self.show_ncs_selections(prefix)
    elif format.lower() == 'phil':
      out_str = self.show_phil_format(prefix=prefix,header=header)
      print >> log, out_str
    elif format.lower() == 'spec':
      # Does not add prefix in SPEC format
      out_str = self.show_search_parameters_values(prefix) + '/n'
      out_str += self.show_chains_info(prefix) + '\n'
      out_str += '\n' + prefix + 'NCS object "display_all"'
      print >> log, out_str
      spec_obj = self.get_ncs_info_as_spec(write=False)
      out_str += spec_obj.display_all(log=log)
    elif format.lower() == 'summary':
      out_str = [self.show_chains_info(prefix)]
      out_str.append(self.show_ncs_headers(prefix))
      out_str.append(self.show_transform_info(prefix))
      out_str = '\n'.join(out_str)
      print >> log, out_str
    return out_str

  def show_phil_format(self,prefix='',header=True,group_prefix=''):
    """
    Returns a string of NCS groups phil parameters

    Args:
      prefix (str): a string to be added, padding the output, at the left of
        each line
      header (bool): When True, include header
      group_prefix (str): prefix for the group only
    """
    str_out = []
    if header:
      msg = '\n{}NCS phil parameters:'
      str_out = [msg.format(prefix),'-'*len(msg)]
    str_line = prefix + '  {:s} = {}'
    str_ncs_group =  prefix + group_prefix + 'ncs_group {\n%s' + prefix + '\n}'
    master_sel_str = sorted(self.ncs_to_asu_selection)
    for m_str in master_sel_str:
      gr = self.ncs_to_asu_selection[m_str]
      str_gr = [str_line.format('reference',m_str)]
      for c_str in gr:
        str_gr.append(str_line.format('selection',c_str))
      str_gr = '\n'.join(str_gr)
      str_out.append(str_ncs_group%str_gr)
    str_out = '\n'.join(str_out)
    return str_out

  def show_search_parameters_values(self,prefix=''):
    """
    Returns a string of search parameters values

    Args:
      prefix (str): a string to be added, padding the output, at the left of
        each line
    """
    list_of_values = [
      'chain_max_rmsd',
      'residue_match_radius',
      'chain_similarity_threshold']
    str_out = ['\n{}NCS search parameters:'.format(prefix),'-'*51]
    str_line = prefix + '{:<35s}:   {}'
    for val in list_of_values:
      s = str_line.format(val, self.__getattribute__(val))
      str_out.append(s)
    str_out.append('. '*26)
    str_out = '\n'.join(str_out)
    return str_out

  def show_chains_info(self,prefix=''):
    """
    Returns formatted string for print out, string containing chains IDs in a
    table format, padded from the left with "prefix"

    Args:
      prefix (str): a string to be added, padding the output, at the left of
        each line
    """
    ids = sorted(self.model_unique_chains_ids)
    str_out = ['\n{}Chains in model:'.format(prefix),'-'*51]
    n = len(ids)
    item_in_row = 10
    n_rows = n // item_in_row
    last_row = n % item_in_row
    str_ids = [prefix + '{:5s}' * item_in_row] * n_rows
    str_ids_last = prefix + '{:5s}' * last_row
    # connect all output stings
    str_out.extend(str_ids)
    str_out.append(str_ids_last)
    str_out.append('. '*26)
    str_out = '\n'.join(str_out)
    str_out = str_out.format(*ids)
    return str_out

  def show_transform_info(self,prefix=''):
    """
    Returns formatted string for print out, string containing chains IDs in a
    table format, padded from the left with "prefix"

    Args:
      prefix (str): a string to be added, padding the output, at the left of
        each line
    """
    str_out = ['\n{}Transforms:'.format(prefix),'-'*51]
    str_line = prefix + '{:<25s}:   {}'
    str_r = prefix + 'ROTA  {:2}{:10.4f}{:10.4f}{:10.4f}'
    str_t = prefix + 'TRANS   {:10.4f}{:10.4f}{:10.4f}'
    ncs_group_n = sorted(self.ncs_group_map)
    for i in ncs_group_n:
      str_out.append(str_line.format('Group #',i))
      gr = self.ncs_group_map[i]
      transform_numbers = sorted(gr[1])
      for j,tr_n in enumerate(transform_numbers):
        tr = self.ncs_transform[tr_n]
        str_out.append(str_line.format('Transform #',j + 1))
        str_out.append(str_line.format('RMSD',tr.rmsd))
        rot = [str_r.format(k,*x) for k,x in enumerate(tr.r.as_list_of_lists())]
        str_out.extend(rot)
        tran = str_t.format(*[x for xi in tr.t.as_list_of_lists() for x in xi])
        str_out.append(tran)
        str_out.append('~ '*20)
    str_out.pop()
    str_out = '\n'.join(str_out)
    return str_out

  def show_ncs_selections(self,prefix=''):
    """
    Return NCS selection strings as a string, for printing

    Args:
     prefix (str): a string to be added, padding the output, at the left of
       each line
    """
    str_out = ['\n{}NCS selections:'.format(prefix),'-'*51]
    str_line = prefix + '{:<25s}:   {}'
    ncs_group_n = sorted(self.ncs_group_map)
    for i in ncs_group_n:
      gr = self.ncs_group_map[i]
      if len(gr) == 3:
        m_str = gr[2]
        gr_copies = self.ncs_to_asu_selection[m_str]
        str_out.append(str_line.format('Group #',i))
        str_out.append(str_line.format('Master selection string',m_str))
        for c_str in gr_copies:
          str_out.append(str_line.format('Copy selection string',c_str))
    transforms_info = self.show_transform_info(prefix)
    str_out.append(transforms_info)
    str_out.append('. '*26)
    str_out = '\n'.join(str_out)
    return str_out

  def show_ncs_headers(self,prefix):
    """
    Returns a string of general info about NCS groups

    Args:
     prefix (str): a string to be added, padding the output, at the left of
       each line
    """
    str_out = ['\n{}NCS summary:'.format(prefix),'-'*51]
    str_line = prefix + '{:<25s}:   {}'
    s = str_line.format('Number of NCS groups', self.number_of_ncs_groups)
    str_out.append(s)
    ncs_group_n = sorted(self.ncs_group_map)
    for i in ncs_group_n:
      gr = self.ncs_group_map[i]
      str_out.append(str_line.format('Group #', i))
      str_out.append(str_line.format('Number of copies', len(gr[1])))
      if len(gr) == 3:
        m_str = gr[2]
        cim = ', '.join(chains_in_string(m_str))
        cic = ', '.join(chains_in_string(self.ncs_to_asu_selection[m_str]))
        str_out.append(str_line.format('Chains in master',cim))
        str_out.append(str_line.format('Chains in copies',cic))
    str_out.append('. '*26)
    str_out = '\n'.join(str_out)
    return str_out

  def __repr__(self,prefix=''):
    """ print NCS object info, padded with "prefix" on the left """
    str_out = [self.show_search_parameters_values(prefix)]
    str_out.append(self.show_chains_info(prefix))
    str_out.append(self.show_ncs_headers(prefix))
    # print transforms
    str_out = '\n'.join(str_out)
    return str_out

def chains_in_string(s):
  """
  Returns a string of chain IDs from a selection string or a selection
  string list

  >>> chains_in_string('chain D or (chain F and (resseq 2:10))')
  ['D', 'F']

  >>> chains_in_string(['chain D','(chain F and (resseq 2:10))'])
  ['D', 'F']
  """
  if isinstance(s,set): s = list(s)
  if isinstance(s,list): s = ' '.join(s)
  chain_list = get_list_of_chains_selection(s)
  chain_set = {x.split()[1] for x in chain_list}
  chain_set = [x.strip() for x in chain_set]
  return sorted(chain_set)

def add_to_dict(d,k,v):
  if d.has_key(k):
    d[k].append(v)
  else:
    d[k] = [v]
  return d

def format_80(s):
  """
  Split string that is longer than 80 characters to several lines

  Args:
    s (str)

  Returns:
    ss (str): formatted string
  """
  i = 0
  ss = ''
  for x in s:
    ss += x
    i += 1
    if i == 80:
      i = 0
      ss += ' \ \n'
  return ss

def inverse_transform(r,t):
  r = r.transpose()
  t = - r*t
  return r,t

def get_list_of_chains_selection(selection_str):
  """
  Args:
    selection_str: (str) selection string

  Returns:
    (list of str) of the format ['chain X', 'chain Y',...]
  """
  # selection_str = "chain ' '"
  sstr = selection_str.replace(')',' ')
  sstr = sstr.replace('CHAIN','chain')
  sstr = sstr.replace('Chain','chain') + ' '
  pos_list = [x.end() for x in re.finditer('chain ',sstr)]
  ch_id_list = []
  # sstr[i:sstr.find(' ',i)]
  for i in pos_list:
    new_el = None
    if sstr.find("'",i+1) > 0:
      new_el = sstr[i:sstr.find("'",i+1)+1]
    else:
      new_el = sstr[i:sstr.find(' ',i)]
    ch_id_list.append(new_el)
  # ch_id_list = [sstr[i:sstr.find("'",i+1)+1] for i in pos_list]
  chain_list = ['chain ' + x for x in ch_id_list]
  return chain_list

def separate_selection_string(s):
  s.replace('Chain ','chain ')
  s.replace('CHAIN ','chain ')
  s.replace(' or chain ','chain ')
  s.replace(') or (chain ','chain ')
  s.replace(')or(chain ','chain ')
  if (s[-1] == ')') and (s[0] == '('): s = s[:-1]
  s = s.split('chain ')
  s_list = []
  for sel_str in s:
    sel_str = sel_str.strip()
    if sel_str.endswith(' or'): sel_str = sel_str[:-3]
    if not sel_str in ['','(']:
      new_s = 'chain ' + sel_str
      if new_s.count('(') < new_s.count(')'):
        new_s = '('+ new_s
      assert new_s.count('(') == new_s.count(')')
      s_list.append(new_s)
  return s_list

def get_pdb_header(pdb_str):
  """
  Collect CRYST and SCALE records

  Args:
    pdb_str: (str) pdb type string

  Returns:
    the portion of the pdb_str till the first ATOM
  """
  pdb_str = pdb_str.splitlines()
  pdb_header_lines = []
  for x in pdb_str:
    if x.startswith('ATOM'): break
    else: pdb_header_lines.append(x)
  return '\n'.join(pdb_header_lines)

def get_center_orth(xyz,selection):
  """
  Compute the center of coordinates of selected coordinates

  Args:
    xyz (flex.vec3_double): Atoms coordinates
    selection (flex.bool): Atom selection array

  Returns:
    (tuple) center of coordinates for the selected coordinates
    Returns (-100,-100,-100) when selection is bad
  """
  try:
    new_xyz = xyz.select(selection)
    mean = new_xyz.mean()
  except RuntimeError:
    mean = (-100,-100,-100)
  return mean

def format_num_as_str(n):
  """  return a 10 digit string of n
  This is a cruicial function because all the NCS search code relies on
  ability to use standard python sort() function to sort whatever is made
  by this.
  """
  if n > 9999999999 or n < 0:
    raise IOError('Input out of the range 0 - 999999999.')
  else:
    return "%010d" % int(n)


def ncs_only(transform_info):
  """
  Verify that all transforms are not present
  (excluding the identity transform)

  Args:
    transform_info: (transformation object)

  Returns:
    (bool): True if all transforms are not present
  """
  present = False
  if transform_info:
    ti = transform_info
    for (r,t,n,cp) in zip(ti.r,ti.t,ti.serial_number,ti.coordinates_present):
      if not is_identity(r,t):
        present = present or cp
  return not present

def is_identity(r,t):
  """ test if r, rotation matrix is identity, and t, translation is zero """
  return r.is_r3_identity_matrix() and t.is_col_zero()

def all_ncs_copies_present(transform_info):
  """
  Check if all transforms coordinates are present,
  if the complete ASU is present

  Args:
    transform_info: (transformation object)

  Returns:
    (bool)
  """
  test = True
  for cp in transform_info.coordinates_present:
    test = test and cp
  return test

def sensible_unit_cell_volume(
        pdb_h=None,
        pdb_inp=None,
        crystal_symmetry=None,
        transform_info=None,
        rotations=None):
  """
  Rough evaluation if the number of atoms of all NCS copies can fit in
  the unit cell.

  Use this only when the pdb_hierarchy contains a single NCS copy

  Args:
    crystal_symmetry
    pdb_h
    transform_info
    rotations

  Returns:
    (bool): False indicates that the complete ASU does not fit in the unit cell
  """
  # fixme : finish function and add test
  if pdb_h is None and pdb_inp is None:
    raise Sorry('Need to provide pdb_h or pdb_inp object')
  n_transforms = 0
  cs = crystal_symmetry
  hierarchy = pdb_h
  if pdb_inp is not None:
    cs = pdb_inp.crystal_symmetry()
    hierarchy = pdb_inp.construct_hierarchy()

  # todo:  check units of unit_cell().volume()
  if cs:
    n_atoms_in_ncs = hierarchy.atoms_size()
    unit_cell_volume = cs.unit_cell().volume()
    # get z, the number of ASU in the cell unit
    space_group = cs.space_group_info()
    z = space_group.type().number()
    if transform_info:
      for r,cp in zip(transform_info.r,transform_info.coordinates_present):
        if (not r.is_r3_identity_matrix()) and (not cp):
          n_transforms +=1
    elif rotations:
      for r in rotations:
        if not r.is_r3_identity_matrix():
          n_transforms += 1
    # Approximate the volume of an atom as 4*pi(1.5A)^3/3
    atom_r = 1.5
    v_atom = 4*math.pi*(atom_r**3)/3
    all_atoms_volume_estimate = (v_atom * n_atoms_in_ncs * z) * n_transforms
    if unit_cell_volume:
      test = (all_atoms_volume_estimate < unit_cell_volume)
  return True

def uniqueness_test(unique_selection_set,new_item):
  """
  When processing phil parameters. Insert new item to set, if not there,
  raise an error if already in set

  Args:
    unique_selection_set: (set)
    new_item: (str)

  Returns:
    unique_selection_set: updated set
  """
  if new_item in unique_selection_set:
    raise IOError,'Phil selection strings are not unique !!!'
  else:
    unique_selection_set.add(new_item)
    return unique_selection_set

def update_selection_ref(selection_ref,new_selection):
  """
  Test for overlapping selection and then updates the selection_ref
  with the new_selection

  Both received and return arguments are flex.bool
  """
  test = (selection_ref & new_selection).count(True) == 0
  assert test,'Overlapping atom selection. Check phil parameters...\n'
  return selection_ref | new_selection

def get_ncs_group_selection(chain_residue_id):
  """
  Args:
    chain_residue_id: [[chain id's],[[[residues range]],[[...]]]

  Returns:
    selection lists, with selection string for each ncs copy in the group
  """
  chains = chain_residue_id[0]
  res_ranges = chain_residue_id[1]
  assert len(chains) == len(res_ranges)
  ncs_selection = []
  for c,rr in zip(chains, res_ranges):
    c = c.strip()
    assert c.find(' ') < 0,'Multiple chains in a single spec ncs group\n'
    ch_selection = 'chain ' + c
    res_range = ['resseq {0}:{1}'.format(s,e) for s,e in rr]
    res_range = '(' + ' or '.join(res_range) + ')'
    ncs_selection.append(ch_selection + ' and ' + res_range)
  return ncs_selection

def get_transform_order(transform_to_ncs):
  """ order transforms mainly for proper chain naming """
  transform_order = sorted(transform_to_ncs)
  transform_chain_assignment = []
  for tr_id in transform_order:
    for tr_selection in transform_to_ncs[tr_id]:
      transform_chain_assignment.append(tr_selection)
  return transform_chain_assignment

def update_ncs_group_map(
        ncs_group_map, ncs_group_id, selection_ids, transform_id):
  """
  Update ncs_group_map that maps a group ID to a list:
   [master selection,list of objects containing transforms and copies
   selections]
  """
  if isinstance(selection_ids, str): selection_ids = [selection_ids]
  if ncs_group_map.has_key(ncs_group_id):
    ncs_group_map[ncs_group_id][0].update(set(selection_ids))
    ncs_group_map[ncs_group_id][1].add(transform_id)
  else:
    ncs_group_map[ncs_group_id] = [set(selection_ids),{transform_id}]
  return ncs_group_map

def sort_dict_keys(d):
  """ sort dictionary d by values """
  return sorted(d,key=lambda k:d[k])

def insure_identity_is_in_transform_info(transform_info):
  """
  Add the identity matrix in cases where the pdb or mmcif files do not
  contain it

  Args:
    transform_info (transformation object): contain rotation, translation,
      serial number and indication if present

  Return:
    transform_info : Add or reorder the transform_info so that the
      identity matrix has serial number 1
  """
  if transform_info is None:
    return None
  ti = transform_info
  ti_zip =  zip(ti.r,ti.t,ti.serial_number,ti.coordinates_present)
  identity_sn = []
  t_r = []
  t_t = []
  t_cp = []
  for i,(r,t,sn,cp) in enumerate(ti_zip):
    if is_identity(r=r,t=t):
      identity_sn.append([i,sn])
      if (i == 0) and (sn == 1):
        t_r.append(r)
        t_t.append(t)
        t_cp.append(cp)
    else:
      t_r.append(r)
      t_t.append(t)
      t_cp.append(cp)
  if identity_sn == [[0,1]]: return transform_info
  # identity transform is missing or not in the first location
  # add identity transform as the first transform
  t_r.insert(0,matrix.sqr([1,0,0,0,1,0,0,0,1]))
  t_t.insert(0,matrix.col([0,0,0]))
  t_cp.insert(0,True)
  # re-assign serial numbers
  s = '{0:3d}'
  t_sn = [s.format(i+1) for i in range(len(t_cp))]
  ti.r = t_r
  ti.t = t_t
  ti.serial_number = t_sn
  ti.coordinates_present = t_cp
  return ti


class NCS_copy():

  def __init__(self,copy_iselection, rot, tran):
    """
    used for NCS groups list copies

    Attributes:
      iselection (flex.size_t): NCS copy selection
      r (matrix obj): rotation matrix from master to this copy
      t (matrix obj): translation vector from master to this copy
    """
    self.iselection = copy_iselection
    self.r = rot
    self.t = tran

  def deep_copy(self):
    res = NCS_copy(self.iselection.deep_copy(), self.r, self.t)
    return res

  def select(self, selection):
    self.iselection = iselection_select(self.iselection, selection)

def iselection_select(isel, sel):
  # x = flex.bool(sel.size(), isel)
  x = flex.bool(sel.size(), False)
  x.set_selected(isel, True)
  res = x.select(sel).iselection()
  return res

class NCS_restraint_group(object):

  def __init__(self,master_iselection):
    """
    used for NCS groups list

    Attributes:
      master_iselection (flex.size_t): NCS group master copy selection
      copies (list): list of NCS_copy objects
    """
    self.master_iselection = master_iselection
    self.copies = []

  def get_iselections_list(self):
    """
    Returns all iselections in the group in one list
    """
    return [self.master_iselection] + [c.iselection for c in self.copies]

  def get_number_of_copies(self):
    return len(self.copies)

  def deep_copy(self):
    result = NCS_restraint_group(self.master_iselection.deep_copy())
    for c in self.copies:
      result.copies.append(c.deep_copy())
    return result

  def select(self, selection):
    assert isinstance(selection, flex.bool)
    self.master_iselection = iselection_select(self.master_iselection, selection)
    for c in self.copies:
      c.select(selection)

class class_ncs_restraints_group_list(list):
  def __init__(self, *args):
    super(class_ncs_restraints_group_list, self).__init__(*args)

  def deep_copy(self):
    result = class_ncs_restraints_group_list()
    for gr in self:
      result.append(gr.deep_copy())
    return result

  def select(self, selection):
    assert isinstance(selection, flex.bool)
    result = self.deep_copy()
    for gr in result:
      gr.select(selection)
    return result
