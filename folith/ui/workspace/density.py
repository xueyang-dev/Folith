"""Shared workspace rhythm; translation retains its compact editing controls."""
WORKSPACE_DENSITY_CSS = """
[data-testid="stMainBlockContainer"]:has(.tp-workspace-shell) {
 --ws-header-height:62px;
 --ws-toolbar-height:36px;
 --ws-section-gap:24px;
 --ws-compact-gap:8px;
 --ws-inspector-width:340px;
 --ws-row-height:44px;
 --ws-compact-row-height:34px;
 --ws-card-padding:14px;
 --ws-surface-radius:8px;
 width:100% !important; max-width:none !important; padding-left:16px !important; padding-right:16px !important;
}
.st-key-workspace_topbar { min-height:var(--ws-header-height); }
/* Location stays neutral; only the small status glyph carries warning color. */
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] button:not([kind="primary"]) { color:var(--tp-sub) !important; background:transparent !important; }
.st-key-workspace_nav [class*="st-key-workspace_nav_item_"] button:not([kind="primary"]) [data-testid="stIconMaterial"] { color:var(--tp-faint) !important; }
.st-key-workspace_nav [class*="st-key-workspace_nav_item_delivery_attention"] button:not([kind="primary"]) [data-testid="stIconMaterial"] { color:var(--tp-warn) !important; font-size:14px !important; }
/* All contextual rails use one width. Delivery deliberately has no permanent rail. */
@media(min-width:1100px) {
 .stMainBlockContainer [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_context_col) > [data-testid="stColumn"]:last-child:has(.st-key-workspace_context_col) {
  flex:0 0 var(--ws-inspector-width) !important; width:var(--ws-inspector-width) !important; min-width:0 !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-workspace_context_col) > [data-testid="stColumn"]:has(.st-key-workspace_main_col) { flex:1 1 0 !important; min-width:0 !important; }
}
.st-key-workspace_main_col:has(.st-key-workspace_term_list),
.st-key-workspace_main_col:has(.st-key-review_queue_filters) {
 border:0 !important; border-radius:0 !important; box-shadow:none !important; background:transparent !important;
 padding:var(--ws-card-padding) 0 !important; gap:var(--ws-compact-gap) !important;
}
.tp-ws-page-heading { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
.tp-ws-page-heading h2 { font-size:18px !important; line-height:26px; padding:0 !important; margin:0; }
.tp-ws-page-heading > span, .tp-ws-subtle { color:var(--tp-sub); font-size:12px !important; margin:0; line-height:1.6; }
.tp-ws-section-label, .tp-ws-inspector-heading { color:var(--tp-sub); font-size:11px; font-weight:600; }
.st-key-workspace_main_col:has(.st-key-workspace_term_list) [data-testid="stMarkdownContainer"],
.st-key-workspace_main_col:has(.st-key-review_queue_filters) [data-testid="stMarkdownContainer"],
.st-key-term_inspector [data-testid="stMarkdownContainer"],
.st-key-review_inspector [data-testid="stMarkdownContainer"] { margin-bottom:0 !important; }
.st-key-workspace_term_list { gap:0 !important; max-height:calc(100dvh - 180px); overflow-y:auto; scrollbar-gutter:stable; }
.tp-term-list-head { display:grid; grid-template-columns:4.6fr 4.4fr 1fr; gap:8px; min-height:var(--ws-compact-row-height); align-items:center; color:var(--tp-sub); font-size:11px; border-bottom:1px solid var(--tp-hairline); padding:0 6px; }
[class*="st-key-workspace_term_row_"] { border-bottom:1px solid var(--tp-hairline); gap:0 !important; }
[class*="st-key-workspace_term_row_"] [data-testid="stHorizontalBlock"] { align-items:center; gap:8px; flex-wrap:nowrap; }
[class*="st-key-workspace_term_row_"] button { min-height:var(--ws-row-height); border:0 !important; border-radius:0; padding:6px; justify-content:flex-start !important; text-align:left !important; color:var(--tp-ink); background:transparent !important; }
[class*="st-key-workspace_term_row_"] button p { font-size:12px !important; line-height:1.4; text-align:left !important; }
[class*="st-key-workspace_term_row_"]:has(button[kind="primary"]) { background:var(--tp-primary-soft);  }
[class*="st-key-workspace_term_row_"] button[kind="primary"],
[class*="st-key-workspace_term_row_"] button[kind="primary"] p { color:var(--tp-primary) !important; background:transparent; }
.tp-term-list-target, .tp-term-list-count { font-size:12px; line-height:1.5; padding:6px 0; }
.tp-term-list-target small { display:inline-block; margin-left:8px; color:var(--tp-sub); font-size:10px; }
.tp-term-list-count { color:var(--tp-sub); font-variant-numeric:tabular-nums; }
.st-key-term_inspector, .st-key-review_inspector {
 padding:var(--ws-card-padding) !important; border:0; border-left:1px solid var(--tp-hairline); background:transparent; border-radius:0;
 gap:var(--ws-compact-gap) !important;
}
.st-key-term_inspector h3, .st-key-review_inspector h3 { font-size:16px !important; line-height:1.4; margin:0; padding:0 !important; overflow-wrap:anywhere; }
.st-key-term_inspector h4, .st-key-review_inspector h4 { font-size:12px !important; margin:8px 0 0; padding:0 !important; }
.tp-term-preferred { margin:0; font-size:15px !important; }
.tp-ws-facts { display:grid; grid-template-columns:1fr 1fr; gap:8px; padding:8px 0; border-top:1px solid var(--tp-hairline); border-bottom:1px solid var(--tp-hairline); font-size:12px; }
.tp-ws-facts span { color:var(--tp-sub); } .tp-ws-facts b { font-weight:500; text-align:right; }
.tp-term-occurrence { font-size:12px; line-height:1.65; } .tp-term-occurrence p { font-size:12px !important; color:var(--tp-sub); margin:6px 0 0; }
.st-key-term_inspector [data-testid="stExpander"] details,
.st-key-review_inspector [data-testid="stExpander"] details { border:0; border-radius:0; background:transparent; }
.st-key-term_inspector [data-testid="stExpander"] summary,
.st-key-review_inspector [data-testid="stExpander"] summary { padding:4px 0; min-height:28px; }
.st-key-term_inspector [data-testid="stExpander"] summary p,
.st-key-review_inspector [data-testid="stExpander"] summary p { font-size:11px !important; }
.st-key-term_inspector [data-testid="stExpander"] details > div,
.st-key-review_inspector [data-testid="stExpander"] details > div { padding:4px 0; }
.st-key-workspace_context_col:has(.tp-inspector-empty) { background:transparent; border:0; padding:14px 0 !important; }
.st-key-translation_inspector { border-radius:var(--ws-surface-radius) !important; padding:var(--ws-card-padding) !important; }
/* No hero or nested dashboard panels: the queue is a selected list. */
.st-key-review_queue_filters button { min-height:var(--ws-toolbar-height); height:var(--ws-toolbar-height); padding:0 12px; }
.st-key-review_queue_list [role="radiogroup"] { gap:0; }
.st-key-review_queue_list [role="radiogroup"] > div { width:100%; }
.st-key-review_queue_list [data-testid="stRadio"] label { border:0 !important; border-bottom:1px solid var(--tp-hairline) !important; border-radius:0 !important; background:transparent; padding:12px !important; margin:0 !important; width:100%; align-items:flex-start; }
.st-key-review_queue_list [data-testid="stRadio"] label:has(input:checked) { background:var(--tp-primary-soft);  }
.st-key-review_queue_list [data-testid="stRadio"] label p { font-size:12px !important; line-height:1.65; color:var(--tp-sub); }
.st-key-review_queue_list [data-testid="stRadio"] label strong { color:var(--tp-ink); font-size:14px; font-weight:600; }
.tp-review-problem { font-size:13px !important; margin:0; line-height:1.6; }
.tp-review-evidence-pair { border-bottom:1px solid var(--tp-hairline); padding:0 0 8px; font-size:12px; line-height:1.6; }
.tp-review-evidence-pair small { color:var(--tp-sub); display:block; font-size:10px; margin-bottom:4px; }
.tp-review-evidence-pair p { font-size:12px !important; color:var(--tp-sub); margin:4px 0 0; }
.tp-ws-recommendation, .tp-review-proposed { font-size:12px !important; line-height:1.6; margin:0; }
.tp-review-proposed { padding:8px; background:var(--tp-primary-soft); }
.st-key-review_inspector [class*="st-key-review_action_bar_"] { border:0 !important; background:transparent !important; padding:4px 0 !important; gap:8px !important; box-shadow:none !important; position:static; }
.st-key-review_inspector [class*="st-key-review_action_bar_"] [data-testid="stHorizontalBlock"] { gap:4px; flex-wrap:nowrap; }
.st-key-review_inspector [class*="st-key-review_action_bar_"] [data-testid="stColumn"] { min-width:0 !important; }
.st-key-review_inspector button { min-height:var(--ws-compact-row-height); padding:0 6px; border-radius:var(--ws-surface-radius); }
.st-key-review_inspector button p { font-size:11px !important; white-space:nowrap; }
.st-key-review_inspector .tp-review-constraint { display:block; font-size:12px; padding:4px 0; }
/* Popover actions keep their text; only the three inline triggers are icons. */
[data-testid="stPopoverBody"]:has([class*="st-key-cat_action_"]) button { width:100% !important; min-height:var(--ws-compact-row-height,34px) !important; height:auto !important; justify-content:flex-start; padding:6px 10px !important; }
[data-testid="stPopoverBody"]:has([class*="st-key-cat_action_"]) button p { position:static !important; width:auto !important; height:auto !important; clip-path:none !important; white-space:normal; font-size:12px !important; }
[class*="st-key-cat_tools_"] [data-testid="stPopover"] button [data-testid="stIconMaterial"] { display:inline-flex !important; }
/* Delivery IA stays intact; bound artifact cards, not the workflow surface. */
.st-key-delivery_primary_grid, .st-key-delivery_lang_grid { grid-template-columns:repeat(3,minmax(0,340px)) !important; gap:var(--ws-compact-gap) !important; justify-content:start; }
[class*="st-key-tp_card_prim_"], [class*="st-key-tp_card_lang_"] { border-radius:var(--ws-surface-radius) !important; padding:12px 52px 12px 14px !important; min-height:72px !important; }
.st-key-workspace_delivery_col .stDownloadButton button { min-height:var(--ws-compact-row-height); width:34px; padding:0; border-radius:var(--ws-surface-radius); }

[class*="st-key-workspace_term_row_"] button > div { width:100%; justify-content:flex-start !important; }
.st-key-workspace_context_col .tp-inspector-empty { font-size:12px; line-height:1.6; color:var(--tp-sub); padding:0 14px; }
.st-key-review_queue_list, .st-key-review_queue_list [data-testid="stRadio"],
.st-key-review_queue_list [role="radiogroup"], .st-key-review_queue_list label { width:100% !important; max-width:none !important; }
.st-key-review_queue_list label > div:first-child { margin-top:4px; flex-shrink:0; }
.st-key-review_inspector [data-testid="stExpander"], .st-key-term_inspector [data-testid="stExpander"] { border:0 !important; border-radius:0 !important; background:transparent !important; box-shadow:none; }
.st-key-review_inspector [class*="st-key-review_action_bar_"] { box-shadow:none !important; position:static !important; }
.st-key-workspace_delivery_col .tp-card-filename { display:inline-block; max-width:calc(100% - 44px); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:bottom; }

@media(max-width:900px) {
 .st-key-delivery_primary_grid, .st-key-delivery_lang_grid { grid-template-columns:repeat(auto-fit,minmax(240px,1fr)) !important; }
}

.tp-term-example { font-size:12px; line-height:1.5; border-top:1px solid var(--tp-hairline); padding-top:8px; }
.tp-term-example small { color:var(--tp-sub); font-size:10px; }
.tp-term-example p { color:var(--tp-sub); font-size:12px !important; margin:4px 0 0; }
.st-key-workspace_delivery_col .tp-delivery-group-header { margin-top:var(--ws-section-gap); }
.st-key-review_inspector [class*="st-key-review_action_bar_"] .stButton > button { min-height:var(--ws-compact-row-height) !important; }

.st-key-review_queue_list [data-testid="stRadioOption"] { width:100% !important; max-width:none !important; }
.st-key-review_queue_list [data-testid="stRadioOption"] > div { width:100% !important; }
.st-key-review_queue_list [data-testid="stRadioOption"] label { display:flex; }
.tp-term-example > div, .tp-term-example p { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.st-key-term_inspector [data-testid="stCaptionContainer"] p { font-size:11px !important; line-height:1.5; }
"""
