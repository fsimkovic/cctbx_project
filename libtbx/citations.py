from __future__ import division, print_function

'''
Functionality for handling citations
'''

import string

import libtbx.phil
from libtbx import str_utils
from libtbx.utils import to_unicode

# =============================================================================
# PHIL definition for citations
master_citation_phil_str = '''
citation
  .multiple = True
{
  article_id = None
    .type = str
    .optional = False
  caption = None
    .type = str
  authors = None
    .type = str
  title = None
    .type = str
  year = None
    .type = int
  journal = None
    .type = str
  volume = None
    .type = str
  pages = None
    .type = str
  pmid = None
    .type = int
  doi_id = None
    .type = str
  url = None
    .type = str
}
'''
master_citation_phil = libtbx.phil.parse(master_citation_phil_str)

# -----------------------------------------------------------------------------
# PHIL definition for journals
# This is used for providing information in CIF blocks
master_journal_phil_str = '''
journal
  .multiple = True
{
  name = None
    .type = str
    .multiple = True
  name_full = None
    .type = str
  abbrev_CAS = None
    .type = str
    .help = Abbreviated name of the cited journal as given in the \
            Chemical Abstracts Service Source Index.
  id_ASTM = None
    .type = str
    .help = The American Society for Testing and Materials (ASTM) code \
            assigned to the journal cited (also referred to as the CODEN \
            designator of the Chemical Abstracts Service).
  id_CSD = None
    .type = str
    .help = The Cambridge Structural Database (CSD) code assigned to the \
            journal cited.
  id_ISSN = None
    .type = str
    .help = The International Standard Serial Number (ISSN) code assigned to \
            the journal cited.
}
'''
master_journal_phil = libtbx.phil.parse(master_journal_phil_str)

# -----------------------------------------------------------------------------
# Construct common database of citations and journals
# This prevents duplication of citations in individual programs if methods from
# different references are used in multiple programs

citations_and_journals = libtbx.phil.read_default(__file__)
citations = master_citation_phil.fetch(source=citations_and_journals).extract()
citations_db = dict( [ (c.article_id, c) for c in citations.citation ] )

journals_db = dict()
journals = master_journal_phil.fetch(source=citations_and_journals).extract()
for journal in journals.journal:
  for name in journal.name:
    journals_db[name] = journal

# =============================================================================
def format_citation (article) :
  authors = article.authors
  author_list = authors.split(", ")
  if len(author_list) == 1 :
    authors_out = authors
  else :
    authors_out = ", ".join(author_list[:-1]) + ", and %s" % author_list[-1]
  output = "%s." % authors
  if article.year is not None :     output += " (%d)" % article.year
  title = article.title
  if (title is not None) :
    title = title.strip()
    if (not title.endswith(".")) :
      title += "."
    output += " %s" % title
  if article.journal is not None :  output += " %s" % article.journal
  if article.volume is not None :
    if article.journal is not None and 'Acta Cryst. ' in article.journal:
      # special case for Acta Cryst journals to get e.g.:
      #   Acta Cryst. D66
      output += "%s" % article.volume
    else:
      output += " %s" % article.volume
  if article.pages is not None :
    if article.volume is not None : output += ":%s" % article.pages
    else :                          output += ", pp. %s" % article.pages
  if output[-1] != '.' :            output += "."
  return output

# -----------------------------------------------------------------------------
def author_list_with_periods (authors, initials_first=False) :
  author_list = authors.split(", ")
  authors_formatted = []
  for author in author_list :
    names = author.split(" ")
    if len(names) == 1 :
      authors_formatted.append(names[0])
    else :
      initials = names[-1]
      new_initials = ""
      for letter in initials :
        if letter in string.letters :
          new_initials += ("%s." % letter)
        else : # usually '-'
          new_initials += letter
      if initials_first :
        reformatted = "%s %s" % (new_initials, " ".join(names[:-1]))
      else :
        reformatted = "%s %s" % (" ".join(names[:-1]), new_initials)
      authors_formatted.append(reformatted)
  return authors_formatted

# -----------------------------------------------------------------------------
def format_citation_cell (article) :
  author_list = author_list_with_periods(article.authors)
  if len(author_list) == 1 :
    authors_out = author_list[0]
  else :
    authors_out = ", ".join(author_list[:-1]) + ", and %s" % author_list[-1]
  output = "%s" % authors_out # XXX no extra period at end!
  if article.year is not None :     output += " (%d)." % article.year
  title = article.title
  if (title is not None) :
    title = title.strip()
    if (not title.endswith(".")) :
      title += "."
    output += " %s" % title
  if article.journal is not None :  output += " %s" % article.journal
  if article.volume is not None :
    if article.journal is not None and 'Acta Cryst. ' in article.journal:
      # special case for Acta Cryst journals to get e.g.:
      #   Acta Cryst. D66
      output += "%s" % article.volume
    else:
      output += " %s" % article.volume
  if article.pages is not None :
    if article.volume is not None : output += ", %s" % article.pages
    else :                          output += ", pp. %s" % article.pages
  if output[-1] != '.' :    output += "."
  return output

# -----------------------------------------------------------------------------
def format_citation_iucr (article) :
  author_list = author_list_with_periods(article.authors)
  if len(author_list) == 1 :
    authors_out = author_list[0]
  else :
    authors_out = ", ".join(author_list[:-1]) + ", & %s" % author_list[-1]
  output = "%s" % authors_out
  if article.year is not None :     output += " (%d)." % article.year
  if article.journal is not None :  output += " %s" % article.journal
  if article.volume is not None :
    if article.journal is not None and 'Acta Cryst. ' in article.journal:
      # special case for Acta Cryst journals to get e.g.:
      #   Acta Cryst. D66
      output += "%s" % article.volume
    else:
      output += " %s" % article.volume
  if article.pages is not None :
    if article.volume is not None : output += ", %s" % article.pages
    else :                          output += ", pp. %s" % article.pages
  if output[-1] != '.' :            output += "."
  return output

# -----------------------------------------------------------------------------
def format_citation_html (article) :
  if (article.journal is None) :
    raise ValueError("Missing journal name for '%s'." % article.article_id)
  author_list = author_list_with_periods(article.authors, initials_first=True)
  if len(author_list) == 1 :
    authors_out = author_list[0]
  else :
    authors_out = ", ".join(author_list[:-1]) + ", and %s" % author_list[-1]
  title = article.title.strip()
  if (not title.endswith(".")) :
    title += "."
  output = "<b>%s</b> %s. " % (title, authors_out)
  if 'Acta Cryst.' in article.journal:
    journal_ref = "<i>Acta Cryst.</i>"
    journal_section = article.journal.split("Acta Cryst. ")[1]
  else:
    journal_ref = "<i>%s</i>" % article.journal
    journal_section = None
  if (article.volume is not None) :
    if journal_section is not None:
      journal_ref += " %s<b>%s</b>" %(journal_section, article.volume)
    else:
      journal_ref += " <b>%s</b>" % article.volume
  if (article.pages is not None) :
    journal_ref += ", %s" % article.pages
  if (article.year is not None) :
    journal_ref += " (%s)" % article.year
  if (article.url is not None) :
    output += """<a href="%s">%s</a>.""" % (article.url, journal_ref)
  elif (article.doi_id is not None) :
    output += """<a href="http://dx.doi.org/%s">%s</a>.""" % (article.doi_id,
      journal_ref)
  elif (article.pmid is not None) :
    output += """<a href="http://www.ncbi.nlm.nih.gov/pubmed/%s">%s</a>.""" % \
      (article.pmid, journal_ref)
  else :
    output += " %s." % journal_ref
  return output

# -----------------------------------------------------------------------------
def show_citation(article, out=None, max_width=79, format='default'):
  if format == 'default' :
    output = format_citation(article)
  elif format == 'iucr' :
    output = format_citation_iucr(article)
  elif format == 'cell' :
    output = format_citation_cell(article)
  if max_width is None or max_width < 1 :
    print(to_unicode(output), file=out)
  else :
    for line in str_utils.line_breaker(output, max_width) :
      print(to_unicode(line), file=out)
  print(to_unicode(''), file=out)

def show_citations(articles, out=None, max_width=79, sort_by_name=True,
                   format='default'):
  if (sort_by_name):
    articles.sort(lambda x, y: cmp(x.authors, y.authors))
  for article in articles:
    show_citation(article, out, max_width, format)

# -----------------------------------------------------------------------------
def citations_as_cif_block(articles, cif_block=None):
  import iotbx.cif.model
  if cif_block is None:
    cif_block = iotbx.cif.model.block()
  def replace_none_with_question_mark(s):
    if s is None: return '?'
    return s
  citation_loop = iotbx.cif.model.loop(header=(
    '_citation.id', '_citation.title', '_citation.journal_abbrev',
    '_citation.journal_volume', '_citation.page_first', '_citation.page_last',
    '_citation.year', '_citation.journal_id_ASTM', '_citation.journal_id_ISSN',
    '_citation.journal_id_CSD'))
  for article in articles:
    if article.pages is None:
      first_page, last_page = "?", "?"
    else:
      pages = article.pages.split('-')
      first_page = pages[0]
      if len(pages) == 1:
        last_page = '?'
      else:
        assert len(pages) == 2
        last_page = pages[1]
    journal = journals_db.get(article.journal)
    assert journal is not None
    citation_loop.add_row(
      {'_citation.id': article.article_id,
       '_citation.title': article.title,
       '_citation.journal_abbrev': journal.abbrev_CAS,
       '_citation.journal_volume': article.volume,
       '_citation.page_first': first_page,
       '_citation.page_last': last_page,
       '_citation.year': article.year,
       '_citation.journal_id_ASTM':
         replace_none_with_question_mark(journal.id_ASTM),
       '_citation.journal_id_ISSN':
         replace_none_with_question_mark(journal.id_ISSN),
       '_citation.journal_id_CSD':
         replace_none_with_question_mark(journal.id_CSD),
       })
  cif_block.add_loop(citation_loop)
  return cif_block

# =============================================================================
# end
