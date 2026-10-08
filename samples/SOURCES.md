# Sample sources

Provenance for every file in `samples/`. This file is **machine-read** by the
pipeline (`dpp.sources`): each `## <logical path>` heading is followed by
`- key: value` lines, and the `publisher` / `date` values are copied onto every
unit extracted from that file.

Nothing here is inferred. Where a value is not stated by the publisher, it reads
`unknown` — the pipeline then stores `unknown`, and `search`/`read` display it as
such. Archive members are addressed as `<archive>.zip!/<member>`.

Recognised keys: `title`, `publisher`, `date`, `source_url`, `license`, `notes`.

---

## usgs_sir2016-5092_corrosivity.pdf
- title: Potential corrosivity of untreated groundwater in the United States
- publisher: U.S. Geological Survey
- date: 2016-07-12
- source_url: https://pubs.usgs.gov/sir/2016/5092/sir20165092.pdf
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: Scientific Investigations Report 2016-5092 (doi:10.3133/sir20165092), by Belitz, Jurgens and Johnson. Text-based PDF. Sample role: tables whose column headings carry units (mg/L, µg/L, µS/cm).

## usgs_sir2020-5116_nwqn.pdf
- title: Quality of data from the U.S. Geological Survey National Water Quality Network for water years 2013-17
- publisher: U.S. Geological Survey
- date: 2020-12-02
- source_url: https://pubs.usgs.gov/sir/2020/5116/sir20205116.pdf
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: Scientific Investigations Report 2020-5116 (doi:10.3133/sir20205116), by Medalie and Bexfield. Text-based PDF. Sample role: second table-heavy document, units in headings.

## nasa_naca_report_1135_compressible_flow.pdf
- title: Equations, tables, and charts for compressible flow
- publisher: National Advisory Committee for Aeronautics (NACA), Ames Aeronautical Laboratory
- date: 1953-01-01
- source_url: https://ntrs.nasa.gov/api/citations/19930091059/downloads/19930091059.pdf
- license: Public domain — "Work of the US Gov. Public Use Permitted." (NTRS rights statement)
- notes: NACA Report 1135, NTRS accession 19930091059. Authored by the Ames Research Staff; NTRS lists no individual authors. Sample role: figures and charts with numbered captions, plus dense numeric tables.

## usgs_wri1992-4036_water_quality_recon.pdf
- title: Reconnaissance investigation of water quality, bottom sediment, and biota associated with irrigation drainage in the Sacramento National Wildlife Refuge Complex, California, 1988-89
- publisher: U.S. Geological Survey
- date: 1992
- source_url: https://pubs.usgs.gov/wri/1992/4036/report.pdf
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: Water-Resources Investigations Report 92-4036, by Dileanis, Sorenson, Schwarzbach and Maurer. Publisher gives the year only, so no month/day is recorded. A page-image scan that already carries an OCR text layer. Sample role: diagrams and figures with captions.

## usgs_wsp0234_conservation_1909.pdf
- title: Papers on the conservation of water resources (reprinted from report of the National Conservation Commission, February, 1909)
- publisher: U.S. Geological Survey / Government Printing Office
- date: 1909
- source_url: https://pubs.usgs.gov/wsp/0234/report.pdf
- license: Public domain — work of the U.S. Government, published 1909
- notes: Water-Supply Paper 234, by Gannett, Leighton, Steuart, Newell, Mendenhall, Stabler and Parker. Publisher gives the year only. A genuine 1909 page-image scan with an OCR text layer over the body text; its map and plate pages carry no extractable text. Sample role: real scan with image-only pages that the sparse-page rule must catch.

## usgs_fs2018-3024_imageonly_scan_derived.pdf
- title: Lahar — River of volcanic mud and debris (first 3 pages, rasterised)
- publisher: U.S. Geological Survey
- date: 2018-05-09
- source_url: https://pubs.usgs.gov/fs/2018/3024/fs20183024.pdf
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: DERIVED, not downloaded as-is. Fact Sheet 2018-3024 (doi:10.3133/fs20183024), by Major, Pierson and Vallance. Built by `tools/make_derived_samples.py`, which rasterises the first 3 pages at 200 dpi to JPEG and discards the text layer; the script asserts 0 extractable characters afterwards. Reason: every government scan we tested (including the two above) already ships an OCR text layer, so none of them forces the OCR path on pages of real body text. Sample role: image-only pages where OCR must recover substantive text. The title/publisher/date above belong to the parent document.

## epa_dpcr_dataset_description.docx
- title: Shanks_dPCR Dataset Description_FINAL_04142025.docx
- publisher: U.S. Environmental Protection Agency, Office of Research and Development
- date: 2025-04-15
- source_url: https://pasteur.epa.gov/uploads/10.23719/1532186/Shanks_dPCR%20Dataset%20Description_FINAL_04142025.docx
- license: EPA ScienceHub license — https://pasteur.epa.gov/license/sciencehub-license.html
- notes: Dataset description accompanying the EPA "Enterococcus Digital PCR Dataset" (catalog.data.gov/dataset/enterococcus-digital-pcr-dataset). The publisher states no document title beyond the filename, so the filename is recorded as the title. Sample role: Office Open XML (.docx) input; also exercises the "no fixed pagination" case.

## fws_wc_report_form.doc
- title: Automated WC Report Form (SD)
- publisher: U.S. Fish and Wildlife Service
- date: unknown
- source_url: https://www.fws.gov/sites/default/files/documents/Automated%20WC%20Report%20Form%20%28SD%29.doc
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: Genuine legacy binary Word document (OLE2 compound file, magic D0 CF 11 E0). The FWS media page states no date or rights text, so `date` is unknown. Sample role: legacy .doc requiring LibreOffice conversion; without LibreOffice the pipeline must record `source_dependent`.

## usgs_factsheets_bundle.zip
- title: Two USGS fact sheets, bundled
- publisher: U.S. Geological Survey
- date: unknown
- source_url: unknown
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: DERIVED container, not downloaded as-is. Built by `tools/make_derived_samples.py` from the two fact sheets below, which were downloaded verbatim. The archive itself has no publication date or URL of its own, so both read `unknown`. Sample role: .zip archive whose members must be inventoried with a reference back to this parent.

## usgs_factsheets_bundle.zip!/fs20163062.pdf
- title: The role of snowpack, rainfall, and reservoirs in buffering California against drought effects
- publisher: U.S. Geological Survey
- date: 2016-08-29
- source_url: https://pubs.usgs.gov/fs/2016/3062/fs20163062.pdf
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: Fact Sheet 2016-3062 (doi:10.3133/fs20163062), by Johannis, Flint, Dettinger, Flint and Ochoa.

## usgs_factsheets_bundle.zip!/fs2013-3105.pdf
- title: The 3D Elevation Program — summary for Alabama
- publisher: U.S. Geological Survey
- date: 2013
- source_url: https://pubs.usgs.gov/fs/2013/3105/pdf/fs2013-3105.pdf
- license: Public domain — work of the U.S. Government (17 U.S.C. §105)
- notes: Fact Sheet 2013-3105 (doi:10.3133/fs20133105), by Carswell. Publisher gives the year only.
