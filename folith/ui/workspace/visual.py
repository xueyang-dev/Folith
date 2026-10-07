"""Navigation and segment-decision presentation; no CAT sizing or app-wide rules.

Keep rail rules here. Document/task surfaces and segment row geometry remain
owned by styles.py. Every selector is rooted in an explicit workspace region.
"""
WORKSPACE_VISUAL_CSS = """
[data-testid="stMainBlockContainer"]:has(.st-key-workspace_document_col) {
 --tp-ws-control-main:32px; --tp-ws-control-compact:28px;
 --tp-ws-divider:var(--tp-hairline-strong);
 --tp-ws-rail-bg:var(--tp-canvas-soft); --tp-ws-selected:var(--tp-primary-soft);
}
/* The rails have one boundary, not a surface for every section. */
.st-key-workspace_document_col { padding:4px 10px 8px 0; }
.st-key-workspace_document_col [role="radiogroup"] { display:flex; flex-wrap:nowrap !important; gap:0; }
.st-key-workspace_document_col [role="radio"] { min-width:0 !important; flex:1 1 0; padding:0 5px !important; }
.st-key-workspace_document_col [data-testid="stSegmentedControl"] button { min-width:0; padding:0 8px; }
.st-key-workspace_document_col [data-testid="stSegmentedControl"] p { font-size:12px !important; white-space:nowrap; }
.st-key-workspace_document_outline_items,
.st-key-workspace_document_block_items,
.st-key-workspace_document_segments { gap:2px !important; }
.st-key-workspace_document_col [data-testid="stExpander"],
.st-key-workspace_document_col [data-testid="stExpander"] details {
 border:0 !important; border-radius:0 !important; background:transparent !important; box-shadow:none !important;
}
.st-key-workspace_document_col [data-testid="stExpander"] summary {
 min-height:34px; padding:6px 4px; border-radius:4px;
}
.st-key-workspace_document_col [data-testid="stExpander"] summary:hover { background:var(--tp-tint-hover); }
.st-key-workspace_document_col [data-testid="stExpander"] summary p {
 font-size:12px !important; line-height:1.5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.st-key-workspace_document_col [data-testid="stExpander"] details[open] > summary { background:var(--tp-primary-soft); color:var(--tp-primary); font-weight:600; }
.st-key-workspace_document_col [data-testid="stExpander"] details > div { padding:2px 0 4px 12px; border-left:1px solid var(--tp-hairline-strong); margin-left:12px; }
.st-key-workspace_document_col [data-testid="stExpander"] [data-testid="stVerticalBlock"] { gap:0 !important; }
.st-key-workspace_document_col .stButton button {
 min-height:28px; padding:3px 6px; border:0; border-radius:3px; justify-content:flex-start; box-shadow:none;
}
.st-key-workspace_document_col .stButton button p { font-size:11px !important; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; text-align:left; }
.st-key-workspace_document_col .stButton button > div { min-width:0; justify-content:flex-start; }
.st-key-workspace_document_col .stButton button[kind="primary"] { color:var(--tp-primary); background:var(--tp-primary-soft); }
/* Main toolbar icons retain their accessible text and native help tooltip. */
.st-key-translation_toolbar [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:6px; }
.st-key-translation_toolbar [data-testid="stColumn"] { min-width:32px !important; }
.st-key-translation_toolbar button,
.st-key-translation_toolbar [data-testid="stTextInputRootElement"] { min-height:32px; border-radius:5px; }
.st-key-translation_editor_actions button,
.st-key-workspace_main_col [class*="st-key-cat_active_actions_"] button { min-height:28px; height:28px; padding:0 6px; border-radius:4px; }
.st-key-translation_editor_actions [data-testid="stHorizontalBlock"],
.st-key-workspace_main_col [class*="st-key-cat_active_actions_"] [data-testid="stHorizontalBlock"] { flex-wrap:nowrap; }
.st-key-translation_editor_actions [data-testid="stColumn"],
.st-key-workspace_main_col [class*="st-key-cat_active_actions_"] [data-testid="stColumn"] { min-width:28px !important; }
.st-key-translation_toolbar .stButton button p,
.st-key-translation_toolbar [data-testid="stPopover"] button p,
.st-key-translation_editor_actions button p,
.st-key-workspace_main_col [class*="st-key-cat_active_actions_"] button p,
.st-key-translation_source_quality_banner button p,
.st-key-workspace_nav_details button p,
.st-key-translation_inspector [data-testid="stFormSubmitButton"] button p {
 position:absolute !important; width:1px; height:1px; padding:0; overflow:hidden; clip-path:inset(50%); white-space:nowrap;
}
.st-key-translation_toolbar [data-testid="stPopover"] button > div:last-child > svg,
.st-key-workspace_nav_details button > div:last-child > svg { display:none; }
.st-key-workspace_topbar .st-key-workspace_toolbar [data-testid="stColumn"]:last-child { flex:0 0 38px !important; width:38px !important; }
.st-key-translation_source_quality_banner button { min-height:28px; padding:0 6px; }
.st-key-workspace_main_col [class*="st-key-cat_num_"] button { padding:0 2px !important; }
.st-key-workspace_main_col [class*="st-key-cat_num_"] button p { white-space:nowrap; font-size:10px !important; font-variant-numeric:tabular-nums; }
/* Four-layer current segment decision panel. */
.st-key-translation_inspector .tp-inspector-finding { padding:8px 0; border-bottom:1px solid var(--tp-hairline-strong); }
.st-key-translation_inspector .tp-inspector-finding strong { font-size:12px; }
.st-key-translation_inspector .tp-inspector-finding p { font-size:12px !important; line-height:1.6; }
.st-key-translation_inspector .tp-inspector-focus-callout { font-size:11px; color:var(--tp-sub); }

/* Inspector visual ownership: Folith CSS is the single source of truth for
   border, radius, background, padding, and shadow. Streamlit's column wrapper
   and any stVerticalBlockBorderWrapper ancestor are explicitly neutralised so
   there is no double-border or radius conflict when styles change. */
.st-key-workspace_context_col > [data-testid="stVerticalBlock"] > [data-testid="stElementContainer"]:has(.st-key-translation_inspector),
.st-key-workspace_context_col > [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlock"]:has(.st-key-translation_inspector) {
 border:0 !important; border-radius:0 !important; background:transparent !important;
 box-shadow:none !important; padding:0 !important;
}
/* Streamlit subtracts a paragraph margin even for our div-based Markdown.
   With the compact rail gap that makes adjacent text overlap. */
.st-key-translation_inspector [data-testid="stMarkdownContainer"] {
 margin-bottom:0 !important;
}
/* The rail scrolls; its contents must keep their natural height. */
.st-key-workspace_context_col > [data-testid="stLayoutWrapper"]:has(> .st-key-translation_inspector) {
 flex:0 0 auto !important;
}
.st-key-translation_inspector > [data-testid="stElementContainer"],
.st-key-translation_inspector > [data-testid="stLayoutWrapper"] {
 flex-shrink:0 !important;
}
.st-key-translation_inspector {
 flex:0 0 auto !important; height:auto !important; max-height:none !important;
 padding:14px !important; gap:8px !important;
 border:1px solid var(--tp-hairline-strong) !important;
 border-radius:6px !important; background:var(--tp-surface) !important;
 box-shadow:none !important;
}
.st-key-translation_inspector .tp-inspector-segment-head {
 display:flex; align-items:center; justify-content:space-between; gap:8px;
 padding-bottom:10px; margin-bottom:2px; border-bottom:1px solid var(--tp-ws-divider);
}
.st-key-translation_inspector .tp-inspector-segment-head strong { font-size:13px; }
.st-key-translation_inspector .tp-inspector-segment-meta,
.st-key-translation_inspector .tp-translation-inspector-position { color:var(--tp-sub); font-size:11px; }
.st-key-translation_inspector .tp-inspector-segment-meta::before { content:" · "; }
.st-key-translation_inspector .tp-inspector-section { margin:0; padding:0; border:0; }
.st-key-translation_inspector .tp-inspector-section-head { display:flex; align-items:center; justify-content:space-between; }
.st-key-translation_inspector h4 { margin:0; padding:0 !important; line-height:1.5; font-size:13px !important; color:var(--tp-ink); }
.st-key-translation_inspector .tp-translation-decision-count { font-size:12px; color:var(--tp-primary); }
.st-key-translation_inspector .tp-translation-decision-empty { padding:4px 0 8px; font-size:12px; line-height:1.6; color:var(--tp-sub); }
.st-key-translation_inspector .tp-translation-decision-empty span { color:#24845c; margin-right:5px; }
.st-key-translation_inspector .tp-translation-decision-row { padding:10px 0; border-bottom:1px solid var(--tp-ws-divider); }
.st-key-translation_inspector .tp-translation-decision-title { font-size:13px; font-weight:650; }
.st-key-translation_inspector .tp-translation-decision-pair { display:grid; gap:5px; padding:8px 10px; margin-top:8px; background:var(--tp-primary-soft); }
.st-key-translation_inspector .tp-translation-decision-side { display:flex; flex-direction:column; gap:2px; }
.st-key-translation_inspector .tp-translation-decision-side > span { color:var(--tp-sub); font-size:10px; }
.st-key-translation_inspector .tp-translation-decision-side strong { font-size:12px; line-height:1.5; font-weight:500; overflow-wrap:anywhere; }
.st-key-translation_inspector .tp-translation-decision-arrow { color:var(--tp-sub); font-size:11px; }
.st-key-translation_inspector .tp-translation-decision-row > p { font-size:12px !important; line-height:1.65; margin:8px 0 0; }
.st-key-translation_inspector .tp-inspector-related { margin-top:8px; padding-top:12px; border-top:1px solid var(--tp-ws-divider); }
.st-key-translation_inspector .tp-inspector-related h4 { color:var(--tp-sub); font-size:12px !important; font-weight:600; }
.st-key-translation_inspector .tp-inspector-related-row { display:grid; grid-template-columns:46px minmax(0,1fr); gap:8px; padding:4px 0; align-items:baseline; }
.st-key-translation_inspector .tp-inspector-related-key { color:var(--tp-sub); font-size:11px; }
.st-key-translation_inspector .tp-inspector-related-item { padding:3px 0; font-size:12px; line-height:1.55; overflow-wrap:anywhere; }
.st-key-translation_inspector .tp-inspector-related-label { color:var(--tp-sub); }
.st-key-translation_inspector .tp-inspector-related-value { color:var(--tp-ink); font-size:12px; }
.st-key-translation_inspector .tp-inspector-muted { font-size:11px; color:var(--tp-sub); }
.st-key-translation_inspector .tp-inspector-context-snippets { min-width:0; }
.st-key-translation_inspector .tp-inspector-context-preview { font-size:11px !important; line-height:1.5; margin:0 0 5px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; color:var(--tp-sub); }
.st-key-translation_inspector .tp-inspector-context-preview strong { font-weight:500; margin-right:6px; color:var(--tp-sub); }
.st-key-translation_inspector .tp-inspector-preview-full { font-size:12px !important; line-height:1.6; overflow-wrap:anywhere; }
.st-key-translation_inspector .tp-inspector-preview-full strong { display:block; font-size:11px; margin-bottom:4px; }
.st-key-translation_inspector [data-testid="stExpander"],
.st-key-translation_inspector [data-testid="stExpander"] details { border:0 !important; border-radius:0; background:transparent; box-shadow:none; }
.st-key-translation_inspector [data-testid="stExpander"] summary { padding:3px 0; min-height:24px; }
.st-key-translation_inspector [data-testid="stExpander"] summary p { font-size:11px !important; color:var(--tp-sub); }
.st-key-translation_inspector .stButton button[kind="tertiary"] { min-height:26px; padding:0; border:0; background:transparent; color:var(--tp-sub); }
.st-key-translation_inspector .stButton button[kind="tertiary"] p { font-size:11px !important; }
.st-key-translation_inspector [data-testid="stForm"] { padding:10px 0 0 !important; margin-top:4px; border:0; border-top:1px solid var(--tp-ws-divider); border-radius:0; background:transparent; }
.st-key-translation_inspector [data-testid="stForm"] [data-testid="stHorizontalBlock"] { gap:4px; flex-wrap:nowrap; }
.st-key-translation_inspector [data-testid="stForm"] [data-testid="stColumn"] { min-width:28px !important; }
.st-key-translation_inspector [data-testid="stForm"] [data-testid="stTextAreaRootElement"] { border:1px solid var(--tp-hairline); border-radius:5px; background:var(--tp-ws-rail-bg); }
.st-key-translation_inspector [data-testid="stForm"] textarea { height:36px !important; min-height:36px !important; padding:8px !important; font-size:12px !important; resize:none; }
.st-key-translation_inspector [data-testid="stForm"] textarea:focus { height:78px !important; }
.st-key-translation_inspector [data-testid="stFormSubmitButton"] button { min-height:32px; height:32px; width:28px; padding:0; border:0; background:transparent; color:var(--tp-sub); }
.st-key-translation_inspector .tp-inspector-answer { font-size:12px; white-space:pre-wrap; line-height:1.6; }
.st-key-workspace_document_col button:focus-visible,
.st-key-workspace_main_col button:focus-visible,
.st-key-translation_inspector button:focus-visible { outline:2px solid var(--tp-primary) !important; outline-offset:2px; }
"""
