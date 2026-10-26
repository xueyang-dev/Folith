# Native Windows scope evidence

Captured by the Shared Qt Windows job on 2026-10-26, source head `7144a3ef5021bc758cc058d8ccf1c74ac39fe665`. [Workflow and artifact](https://github.com/xueyang-dev/Folith/actions/runs/40237673753). Backend: `windows`, not offscreen. The final PR check reruns the same script for later commits.

The generated DOCX and deterministic provider verify the preparation dialog, plan withdrawal, estimate change, real pipeline, user-skip manifest and restore alignment. See `evidence.json` and the two screenshots. This is scripted native-source evidence, not human-operated acceptance, paid-model quality or an installed-release check.

Reproduce from the repository on Windows:

```powershell
$env:QT_QPA_PLATFORM = "windows"
python scripts/scope_workflow_evidence.py --output scope-workflow-windows
```
