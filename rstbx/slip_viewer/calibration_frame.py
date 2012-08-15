from __future__ import division
# -*- Mode: Python; c-basic-offset: 2; indent-tabs-mode: nil; tab-width: 8 -*-
#
# $Id: calibration_frame.py 289 2012-03-06 05:08:56Z hattne $

import wx


class SBSettingsFrame(wx.MiniFrame):
  def __init__ (self, *args, **kwds) :
    super(SBSettingsFrame, self).__init__(*args, **kwds)
    szr = wx.BoxSizer(wx.VERTICAL)
    panel = SBSettingsPanel(self)
    self.SetSizer(szr)
    szr.Add(panel, 1, wx.EXPAND)
    szr.Fit(panel)
    self.panel = panel
    self.sizer = szr
    self.Fit()
    self.Bind(wx.EVT_CLOSE, lambda evt : self.Destroy(), self)

  # XXX Could have a set_image() function instead of referring back to
  # the frame all the time?


class SBSettingsPanel(wx.Panel):
  # XXX Names: they're not really settings.  XXX Allow for setting
  # rotation, and provide a hierarchical drop-down menu to play with
  # detector, panel, sensor and ASIC.

  def __init__ (self, *args, **kwds) :
    super(SBSettingsPanel, self).__init__(*args, **kwds)
    sizer = wx.BoxSizer(wx.VERTICAL)
    self.SetSizer(sizer)

    # Number of decimal digits for distances.
    self.digits = 2

    # Quad translation controls
    from wx.lib.agw.floatspin import EVT_FLOATSPIN, FloatSpin

    img = self.GetParent().GetParent().pyslip.tiles.raw_image
    for serial in xrange(4):
      fast, slow = img.get_panel_fast_slow(serial)
      name_quadrant = ["Q0", "Q1", "Q2", "Q3"][serial]
      box = wx.BoxSizer(wx.HORIZONTAL)

      for (name_direction, value) in [("fast", fast), ("slow", slow)]:
        name_ctrl = name_quadrant + "_" + name_direction + "_ctrl"

        spinner = FloatSpin(
          self, digits=self.digits, name=name_ctrl, value=value)
        self.Bind(EVT_FLOATSPIN, self.OnUpdateQuad, spinner)

        box.Add(spinner,
                0, wx.RIGHT|wx.TOP|wx.BOTTOM|wx.ALIGN_CENTER_VERTICAL, 5)
        box.Add(wx.StaticText(self, label=name_quadrant + " " + name_direction),
                0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)

        setattr(self, "_" + name_ctrl, spinner)

      sizer.Add(box)

    box = wx.BoxSizer(wx.HORIZONTAL)

    btn = wx.Button(self, label="Restore metrology")
    box.Add(btn, flag=wx.ALL, border=5)
    self.Bind(wx.EVT_BUTTON, self.OnRestoreMetrology, btn)

    btn = wx.Button(self, label="Save current metrology")
    box.Add(btn, flag=wx.ALL, border=5)
    self.Bind(wx.EVT_BUTTON, self.OnSaveMetrology, btn)

    sizer.Add(box, flag=wx.ALIGN_CENTER)

    # XXX Rename to metrology tool?


  def OnRestoreMetrology(self, event):
    dialog = wx.FileDialog(
      self,
      defaultDir="",
      message="Restore metrology file",
      style=wx.FD_OPEN,
      wildcard="Phil files (*.eff; *.def)|*.eff;*.def")
    if dialog.ShowModal() == wx.ID_OK:
      path = dialog.GetPath()
      if (path != "") :
        from cxi_xdr_xes.cftbx.detector.metrology2phil import master_phil
        from cxi_xdr_xes.cftbx.detector.metrology import metrology_as_transformation_matrices
        from libtbx import phil

        frame = self.GetParent().GetParent()
        stream = open(path)
        metrology_phil = master_phil.fetch(sources=[phil.parse(stream.read())])
        stream.close()
        frame.metrology_matrices = metrology_as_transformation_matrices(
          metrology_phil.extract())

        img = frame.pyslip.tiles.raw_image
        img.apply_metrology_from_matrices(frame.metrology_matrices)

        # Update the view.
        frame.load_image(frame._img.file_name) # XXX ugly?

        # Update the controls, remember to reset the default values
        # for the spinners.
        for serial in xrange(4):
          fast, slow = img.get_panel_fast_slow(serial)
          name_quadrant = ["Q0", "Q1", "Q2", "Q3"][serial]

          spinner = getattr(self, "_" + name_quadrant + "_fast_ctrl")
          spinner.SetDefaultValue(fast)
          spinner.SetValue(fast)

          spinner = getattr(self, "_" + name_quadrant + "_slow_ctrl")
          spinner.SetDefaultValue(slow)
          spinner.SetValue(slow)


  def OnSaveMetrology(self, event):
    dialog = wx.FileDialog(
      self,
      defaultDir="",
      message="Save metrology file",
      style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
      wildcard="Phil files (*.def)|*.def")
    if dialog.ShowModal() == wx.ID_OK:
      path = dialog.GetPath()
      if (path != "") :
        from cxi_xdr_xes.cftbx.detector.metrology2phil import master_phil

        # Round-trip the metrology string for pretty-printing.
        frame = self.GetParent().GetParent()
        img = frame.pyslip.tiles.raw_image
        metrology_params = master_phil.format(
          python_object=img.transformation_matrices_as_metrology().extract())
        stream = open(path, "w")
        stream.write(metrology_params.as_str())
        stream.close()
        print "Dumped pickled metrology to ", path


  def OnUpdateQuad(self, event):
    # Get the name of the spinner and its delta, the deviation from
    # the default value.  Update the default for the next event.
    obj = event.EventObject
    name = obj.GetName()
    value = obj.GetValue()
    delta = float(value - obj.GetDefaultValue())
    obj.SetDefaultValue(value)

    # Update the frame's effective metrology parameters.
    frame = self.GetParent().GetParent()
    img = frame.pyslip.tiles.raw_image
    if (name == "Q0_fast_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(0, delta, 0)
    elif (name == "Q0_slow_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(0, 0, delta)
    elif (name == "Q1_fast_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(1, delta, 0)
    elif (name == "Q1_slow_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(1, 0, delta)
    elif (name == "Q2_fast_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(2, delta, 0)
    elif (name == "Q2_slow_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(2, 0, delta)
    elif (name == "Q3_fast_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(3, delta, 0)
    elif (name == "Q3_slow_ctrl"):
      frame.metrology_matrices = img.displace_panel_fast_slow(3, 0, delta)

    # Update the view.
    frame = self.GetParent().GetParent()
    frame.load_image(frame._img.file_name) # XXX ugly?
