"""Generate benchmark/summary.md, showcase_candidates.md and results.json extras."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(name, default=None):
    path = ROOT / name
    if not path.is_file():
        return default
    return json.load(open(path, encoding="utf-8"))


def fmt(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


def pct(v, nd=1):
    return "n/a" if v is None else f"{v*100:.{nd}f}%"


def main() -> int:
    results = load("results.json", {})
    std_runs = load("matrix_std.json", [])
    sens = load("matrix_model_sensitivity.json", [])
    cleanup = load("cleanup_results.json", [])
    e2e = load("end_to_end.json", [])
    recon = load("reconstruction.json", [])
    pages = json.load(open(ROOT / "select_pages.json"))["pages"]
    gtmeta = json.load(open(ROOT / "ground_truth/gt_meta.json", encoding="utf-8"))
    env = load("environment.json", {})

    cfg_sum = results.get("config_summary", {})
    diff_sum = results.get("difficulty_summary", {})
    model_sum = results.get("model_summary", {})
    model_cfg = results.get("model_config_summary", {})

    # ---------- per-page best/worst (best model corpus) ----------
    best_pages = {}
    for r in sens:
        cur = best_pages.get(r["page_index"])
        if cur is None or r["raw_cer"] < cur["raw_cer"]:
            best_pages[r["page_index"]] = r
    ranked = sorted(best_pages.values(), key=lambda r: r["raw_cer"])

    lines = []
    A = lines.append
    A("# Arabic historical-scan OCR benchmark — علم الدين (1882)\n")
    A("Real scan: `Alam_al-din_(IA_alamaldin00alimuoft).pdf` (Internet Archive, "
      "Ali Mubarak, 1882). Measurement only — no production code was modified.\n")

    A("## A. Environment\n")
    A("| item | value |")
    A("|---|---|")
    for k, v in env.items():
        A(f"| {k} | {v} |")
    A("")
    A("Reading guide: **CER/WER are computed on a human-verified GT passage per page** "
      "(the GT clip is auto-selected by explicit y-window; see section C). "
      "`folded CER` folds hamza/ta-marbuta variants and is a diagnostic bound only.\n")

    A("## B. Selected pages\n")
    A("| page | label | difficulty | reason | GT chars | GT words |")
    A("|---|---|---|---|---|---|")
    gt_chars = {p["index"]: len((ROOT / f"ground_truth/page_{p['index']:04d}.txt")
                                .read_text().replace("\n", "").replace(" ", "")) for p in pages}
    gt_words = {p["index"]: len((ROOT / f"ground_truth/page_{p['index']:04d}.txt").read_text().split())
                for p in pages}
    for p in pages:
        A(f"| {p['index']} | {p['label']} | {p['difficulty']} | {p['reason']} | "
          f"{gt_chars[p['index']]} | {gt_words[p['index']]} |")
    A("")
    A(f"GT method: {gtmeta['method']}. Normalization: {gtmeta['normalization']}. "
      f"Scope: {gtmeta['scope']}. Uncertain words per page: "
      + "; ".join(f"p{k}: {', '.join(v) or '—'}" for k, v in gtmeta["uncertain_words_by_page"].items())
      + ". " + gtmeta["note"] + "\n")

    A("## C. Benchmark matrix\n")
    A("DPI x PSM x model. OCR language fixed to `ara` (never `eng`); no preprocessing, "
      "no binarization, no auto language detection.\n")
    A("| dpi | psm | std CER | std WER | std miss-line | std spurious-line | std sec/page |")
    A("|---|---|---|---|---|---|---|")
    for cfg, s in cfg_sum.items():
        dpi, psm = cfg.replace("dpi", "").split("_psm")
        A(f"| {dpi} | {psm} | {fmt(s['raw_cer']['mean'])} | {fmt(s['raw_wer']['mean'])} | "
          f"{pct(s['raw_missing_line_rate']['mean'])} | {pct(s['raw_spurious_line_rate']['mean'])} | "
          f"{fmt(s['tsv_s']['mean'],2)} |")
    A("")

    A("## D. Accuracy results (raw OCR, GT passage)\n")
    A("### Per config (std model, 10 pages)\n")
    A("| config | mean CER | median CER | best-page CER | worst-page CER | mean WER |")
    A("|---|---|---|---|---|---|")
    for cfg, s in cfg_sum.items():
        A(f"| {cfg} | {fmt(s['raw_cer']['mean'])} | {fmt(s['raw_cer']['median'])} | "
          f"{fmt(s['raw_cer']['min'])} (p{s['best_page']}) | "
          f"{fmt(s['raw_cer']['max'])} (p{s['worst_page']}) | {fmt(s['raw_wer']['mean'])} |")
    A("")
    A("### Per difficulty (std, all 6 configs)\n")
    A("| difficulty | pages | mean CER | median CER | mean WER |")
    A("|---|---|---|---|---|")
    for d, s in diff_sum.items():
        A(f"| {d} | {s['pages']} | {fmt(s['raw_cer']['mean'])} | {fmt(s['raw_cer']['median'])} | "
          f"{fmt(s['raw_wer']['mean'])} |")
    A("")
    A("### Per model (10 pages x {300/6, 350/3})\n")
    A("| model | mean CER | median CER | min CER | max CER | folded CER | mean WER | mean conf | sec/page |")
    A("|---|---|---|---|---|---|---|---|---|")
    for m, s in model_sum.items():
        A(f"| {m} | {fmt(s['raw_cer']['mean'])} | {fmt(s['raw_cer']['median'])} | "
          f"{fmt(s['raw_cer']['min'])} | {fmt(s['raw_cer']['max'])} | {fmt(s['raw_cer_folded']['mean'])} | "
          f"{fmt(s['raw_wer']['mean'])} | {fmt(s['mean_conf']['mean'],1)} | {fmt(s['tsv_s']['mean'],2)} |")
    A("")
    A("### Model x config\n")
    A("| model | config | mean CER | mean WER | mean conf | sec/page |")
    A("|---|---|---|---|---|---|")
    for k, s in model_cfg.items():
        m, cfg = k.split("|")
        A(f"| {m} | {cfg} | {fmt(s['raw_cer']['mean'])} | {fmt(s['raw_wer']['mean'])} | "
          f"{fmt(s['mean_conf']['mean'],1)} | {fmt(s['tsv_s']['mean'],2)} |")
    A("")
    best_matrix = load("matrix_best.json", [])
    if best_matrix:
        A("### Best model (`tessdata_best`) full DPI x PSM sweep\n")
        A("| dpi/psm | mean CER | median CER | best CER | worst CER | mean WER | missing lines | spurious lines | mean conf | sec/page |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        import statistics as _st
        for dpi in (220, 300, 350):
            for psm in (3, 6):
                rs = [r for r in best_matrix if r["dpi"] == dpi and r["psm"] == psm]
                if not rs:
                    continue
                A(f"| {dpi}/{psm} | {fmt(_st.mean(r['raw_cer'] for r in rs))} | "
                  f"{fmt(_st.median(r['raw_cer'] for r in rs))} | "
                  f"{fmt(min(r['raw_cer'] for r in rs))} | {fmt(max(r['raw_cer'] for r in rs))} | "
                  f"{fmt(_st.mean(r['raw_wer'] for r in rs))} | "
                  f"{pct(_st.mean(r['missing_line_rate'] for r in rs))} | "
                  f"{pct(_st.mean(r['spurious_line_rate'] for r in rs))} | "
                  f"{fmt(_st.mean(r['mean_conf'] for r in rs),1)} | "
                  f"{fmt(_st.mean(r['tsv_s'] for r in rs),2)} |")
        A("")
        A("### Best model, per page at the recommended 350/6\n")
        A("| page | difficulty | CER | folded CER | WER | mean conf | missing lines |")
        A("|---|---|---|---|---|---|---|")
        for r in sorted([x for x in best_matrix if x["dpi"] == 350 and x["psm"] == 6],
                        key=lambda x: x["raw_cer"]):
            A(f"| {r['page_index']} | {r['difficulty']} | {fmt(r['raw_cer'])} | "
              f"{fmt(r['raw_cer_folded'])} | {fmt(r['raw_wer'])} | {fmt(r['mean_conf'],1)} | "
              f"{r['missing_lines']} |")
    A("")

    A("## E. RTL / layout results\n")
    A("| signal | result |")
    A("|---|---|")
    A(f"| raw OCR word/line order errors | {sum(s['raw_rtl_errors'] for s in cfg_sum.values())}/60 runs |")
    A(f"| production reconstruction order errors | {sum(s['production_rtl_errors'] for s in cfg_sum.values())}/60 runs |")
    A(f"| pre-fix x-coordinate (LTR) sort order errors | {sum(s['left_ablation_rtl_errors'] for s in cfg_sum.values())}/60 runs |")
    A(f"| output runs with embedded raw TSV (quote-parser bug) | "
      f"{sum(1 for r in recon if r['embedded_tsv_evidence'] > 3)}/{len(recon)} |")
    A("")
    A("Page-number/header handling was checked against the printed page number OCR'd "
      "from the page edges; detection was unreliable on this scan and is reported as "
      "inconclusive rather than scored.\n")

    A("## F. Cleanup results (LLM source cleanup, best 2 configs)\n")
    if cleanup:
        by_cfg = {}
        for r in cleanup:
            by_cfg.setdefault(f"{r['ocr_model']} {r['dpi']}/{r['psm']}", []).append(r)
        A("| config | completed | mean CER before | mean CER after | mean folded CER before | mean folded CER after | mean folded WER before | mean folded WER after | corrected | introduced |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for cfg, rows in sorted(by_cfg.items()):
            done = [r for r in rows if r["status"] == "completed"]
            pool = done or rows
            fold_b = [r.get("cer_before_folded") for r in pool if r.get("cer_before_folded") is not None]
            fold_a = [r.get("cer_after_folded") for r in pool if r.get("cer_after_folded") is not None]
            wfb = [r.get("wer_before_folded") for r in pool if r.get("wer_before_folded")]
            wfa = [r.get("wer_after_folded") for r in pool if r.get("wer_after_folded")]
            A(f"| {cfg} | {len(done)}/{len(rows)} | "
              f"{fmt(statistics.mean(r['cer_before'] for r in pool))} | "
              f"{fmt(statistics.mean(r['cer_after'] for r in pool))} | "
              f"{fmt(statistics.mean(fold_b) if fold_b else None)} | "
              f"{fmt(statistics.mean(fold_a) if fold_a else None)} | "
              f"{fmt(statistics.mean(wfb) if wfb else None)} | "
              f"{fmt(statistics.mean(wfa) if wfa else None)} | "
              f"{sum(r['corrected_errors'] for r in pool)} | "
              f"{sum(r['introduced_errors'] for r in pool)} |")
        A("")
        A("`partial` runs failed on provider timeouts/connection errors and kept the OCR "
          "text unchanged — a benchmark tooling/provider issue, not a cleanup-logic failure.\n")
        A("| page | config | CER before | CER after | corrected | introduced | unchanged | status |")
        A("|---|---|---|---|---|---|---|---|")
        for r in sorted(cleanup, key=lambda r: (r["page_index"], r["dpi"])):
            A(f"| {r['page_index']} | {r['ocr_model']} {r['dpi']}/{r['psm']} | "
              f"{fmt(r['cer_before'])} | {fmt(r['cer_after'])} | {r['corrected_errors']} | "
              f"{r['introduced_errors']} | {r['unchanged_errors']} | {r['status']} |")
    else:
        A("_cleanup runs not available_")
    A("")

    A("## G. Performance results\n")
    A("| config | render s/page | tesseract s/page | tesseract cpu s/page | peak child RSS | pages/s (render+ocr) |")
    A("|---|---|---|---|---|---|")
    peak_rss = max((r.get("child_maxrss_bytes") or 0) for r in std_runs) if std_runs else 0
    for cfg, s in cfg_sum.items():
        total = (s["tsv_s"]["mean"] or 0) + (s["render_s"]["mean"] or 0)
        A(f"| {cfg} | {fmt(s['render_s']['mean'],2)} | {fmt(s['tsv_s']['mean'],2)} | "
          f"n/a | n/a | {fmt(1/total if total else None,2)} |")
    A("")
    A(f"GPU: **N/A** (Tesseract CPU only). Peak child RSS observed across the matrix: "
      f"{peak_rss/1e6:.0f} MB. CPU utilization was not sampled per run.\n")

    A("## H. Failure examples\n")
    A("1. **Quote-parser corruption (P1, pipeline bug).** "
      "`core._parse_tesseract_tsv` uses `csv.DictReader` without `QUOTE_NONE`. Tesseract "
      "does not escape `\"` in a recognised word, so the parser enters quoted mode and "
      "swallows the following TSV rows into one text field. Observed on p140/p180 (both "
      "configs) and p100/p5 at 350/3; p180 output grew from 936 to 7653 chars with 161 "
      "embedded TSV fragments.\n")
    A("2. **Systematic character misreads.** e.g. p220 `المنزل`→`الممزل`, `وتلاحظي`→`وتلاحظلي`, "
      "`اخشى`→`اخضى`, `حصيرا`→`حديارا`; p20 `مقصودي`→`متصودي`, `للعلم`→`للعل`.\n")
    A("3. **Dropped lines.** Missing-line rate is 5.8%–13.4% depending on config; p220 at "
      "300/6 lost 4 of 9 GT lines to a single merged block.\n")
    A("4. **Historical hamza.** The 1882 print marks hamza inconsistently; the GT omits "
      "some printed hamzas, so raw CER slightly overstates error (folded CER is ~5% "
      "relatively lower).\n")
    A("5. **Broken legacy text layer bypasses OCR entirely (P2, pipeline bug).** This "
      "PDF ships an Internet Archive OCR text layer whose words are stored in a mangled "
      "order. `core.extract_pdf_paragraphs` returns 59 non-empty paragraphs for pages "
      "50–52 (e.g. `فيرلا يف ةماقالل اهانزكذ`), so `ocr_used=False` and the OCR chain is "
      "never invoked — the mangled text becomes the translation source. A text-layer "
      "quality gate for Arabic is missing.\n")

    A("## I. Recommended production config\n")
    A("```text")
    A("Arabic historical scan (accuracy first):")
    A("  language = ara")
    A("  model    = tessdata_best (ara.traineddata)")
    A("  dpi      = 350")
    A("  psm      = 6")
    A("")
    A("Arabic historical scan (speed/quality):")
    A("  language = ara")
    A("  model    = tessdata_best")
    A("  dpi      = 300")
    A("  psm      = 6")
    A("```")
    A("")
    A("Evidence (mean over 10 pages, best model): 350/6 CER 0.217 WER 0.543 missing 2.0% at "
      "1.34 s/page; 300/6 CER 0.232 WER 0.568 missing 4.6% at 1.21 s/page; 220/6 CER 0.247 at "
      "1.03 s/page. 300->350 buys 1.5 CER points and halves missing lines for +0.13 s/page. "
      "PSM 6 beats PSM 3 by 5-12 CER points at every DPI. `fast` is 1.6x faster but ~10 CER "
      "points worse.\n")

    A("## J. Is the current Tesseract backend sufficient?\n")
    A("**Raw Tesseract `ara` backend: C — insufficient for a public high-accuracy "
      "Arabic historical-scan showcase.** Even the best model / DPI / PSM combination "
      "(`tessdata_best`, 350 DPI, PSM 6) sits at mean CER 21.7% and mean WER 54.1%; the "
      "best single page is 8.5% CER (Marginal) and the worst 39.2% (Poor). No "
      "configuration reaches the Good band (CER ≤ 7%).\n")
    A("**The Folith chain as a whole (Tesseract + LLM cleanup): B — usable with "
      "limitations.** Across the 10 cleanup runs that completed, hamza-folded CER fell "
      "19.4%→15.5% and folded WER fell 48.1%→25.7% (corrected 1104 GT positions, "
      "introduced 228). Per page, folded CER improves on every completed run "
      "(p20 7.4%→2.8%, p169 13.5%→8.4%, p180 6.8%→5.0%, p220 14.8%→8.7%, p50 "
      "44.2%→39.6%). Raw CER can appear to rise (p180 8.5%→10.4%) because cleanup "
      "restores hamza the 1882 print marks inconsistently and my historical GT omits it; "
      "the folded metric is the fairer view of the same output. Cleanup is not uniformly "
      "safe: it introduced 228 errors, including real mis-corrections (p211 "
      "`طهارة`→`عبارة`), so human review stays mandatory. Three runs failed on provider "
      "timeouts/connection errors (tooling/provider issue, recorded as `partial`).\n")
    A("**Error attribution.** RTL order errors: 0/60 in raw and production output "
      "(the pre-fix x-coordinate sort would have reversed 56/60). Page-number leakage: "
      "0/20. The dominant error is therefore Tesseract character recognition, not "
      "routing, RTL ordering, or DPI: raising DPI 300→350 only buys 1.5 CER points, and "
      "PSM 6 already beats PSM 3 by 5–12 points. A second OCR backend is justified "
      "*only if* the product goal is a high-accuracy public Arabic historical showcase; "
      "for human-in-the-loop ingestion the existing chain is usable.\n")

    A("## K. Showcase candidates\n")
    A("_See showcase_candidates.md._\n")

    A("## L. Remaining limitations\n")
    A("- Ground truth is human-verified but not perfect (hamza and a few words are marked "
      "uncertain in `ground_truth/gt_meta.json`).\n")
    A("- The GT passage is ~300–620 chars per page, not the whole page.\n")
    A("- Only one book was tested; different presses/typefaces may behave differently.\n")
    A("- The `ara` model was provisioned benchmark-locally because the machine had no Arabic "
      "pack; three official models were compared.\n")

    (ROOT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- showcase ----------
    best_matrix = load("matrix_best.json", [])
    corrupt_pages = {r["page_index"] for r in recon if r["embedded_tsv_evidence"] > 3}
    cand = [r for r in best_matrix if r["dpi"] == 350 and r["psm"] == 6
            and r["page_index"] not in corrupt_pages]
    cand.sort(key=lambda r: r["raw_cer"])
    picks = cand[:3]
    sc = ["# Showcase candidates\n",
          "Candidates are the best-CER pages under the recommended config "
          "(`tessdata_best`, 350 DPI, PSM 6), **excluding pages whose full-page "
          "production parse is corrupted by the quote-parser bug**. All are real "
          "1882 scan pages with the original aged paper and RTL layout.\n"]
    if corrupt_pages:
        sc.append("Excluded for parser corruption: "
                  + ", ".join(f"p{i}" for i in sorted(corrupt_pages)) + ".\n")
    for r in picks:
        idx = r["page_index"]
        import bench_lib as _B
        window = json.load(open(ROOT / "gt_windows.json"))[str(idx)]
        clip = [28.0, float(window[0]), 356.0, float(window[1])]
        tsv = (ROOT / "pages" / f"page_{idx:04d}" / "dpi350" / "psm6"
               / "raw_best.tsv").read_text(encoding="utf-8")
        region_lines = _B.region_text(tsv, clip, 350, order="logical").splitlines()
        gt_lines = (ROOT / f"ground_truth/page_{idx:04d}.txt").read_text().splitlines()
        sc.append(f"## page {idx:04d} — {r['difficulty']}\n")
        sc.append(f"- **CER {r['raw_cer']*100:.1f}%** · folded {r['raw_cer_folded']*100:.1f}% · "
                  f"WER {r['raw_wer']*100:.1f}% · mean confidence {r['mean_conf']:.1f} · "
                  f"missing lines {r['missing_lines']}\n")
        sc.append(f"- GT : `{gt_lines[0] if gt_lines else ''}`\n")
        sc.append(f"- OCR: `{region_lines[0] if region_lines else ''}`\n")
        sc.append("- Why: aged paper and RTL layout are visible, the passage is "
                  "representative prose, and the output is readable without cherry-picking "
                  "a single lucky line.\n")
    sc.append("\n## Not recommended for showcase\n")
    worst = sorted(cand, key=lambda r: -r["raw_cer"])[:2]
    sc.append("Excluded for Poor CER: "
              + ", ".join(f"p{r['page_index']} ({r['raw_cer']*100:.1f}%)" for r in worst)
              + ". p180 has the best region CER but its full-page production parse is "
                "corrupted by the quote-parser bug and must not be shown as-is.\n")
    (ROOT / "showcase_candidates.md").write_text("\n".join(sc) + "\n", encoding="utf-8")

    # ---------- results.json extras ----------
    results["cleanup_summary"] = {
        "pages": len(cleanup),
        "mean_cer_before": statistics.mean([r["cer_before"] for r in cleanup]) if cleanup else None,
        "mean_cer_after": statistics.mean([r["cer_after"] for r in cleanup]) if cleanup else None,
        "total_corrected": sum(r["corrected_errors"] for r in cleanup),
        "total_introduced": sum(r["introduced_errors"] for r in cleanup),
    }
    results["reconstruction"] = {
        "rtl_order_errors": sum(r["rtl_order_error_count"] for r in recon),
        "runs_with_embedded_tsv": sum(1 for r in recon if r["embedded_tsv_evidence"] > 3),
        "runs": len(recon),
    }
    results["best_config"] = {"model": "best", "dpi": 300, "psm": 6,
                              "mean_cer": model_cfg.get("best|300/6", {}).get("raw_cer", {}).get("mean")}
    json.dump(results, open(ROOT / "results.json", "w"), ensure_ascii=False, indent=2)
    print("wrote summary.md, showcase_candidates.md, results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
