"""wpx — WordPerfect-to-Word migration and form-assembly toolkit.

Pipeline stages:

  1. convert     wpd_convert.py   .wpd -> .docx via WordPerfect 2021 COM
  2. scan        wpx.scan         read the converted corpus, detect field values
  3. catalog     wpx.catalog      roll hits up across documents: which values are
                                  firm boilerplate, which vary per matter
  4. templatize  wpx.templatize   rewrite a .docx into a template whose variable
                                  values are replaced by {{field}} placeholders
  5. matter      wpx.matters      enter each fact once, per matter
  6. generate    wpx.render       fill every template for a matter in one pass

Everything above stage 1 is pure standard library, so it runs on the firm's
Windows box, a Mac, or CI without a package install.
"""

__version__ = "0.2.0"
