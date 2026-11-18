# SIM licence read — Wave 7 Build 3 (plan-effect prediction)

- **What was built:** `core/sim_predict.py` + `core/sim_tools.py`,
  own VYUK code, stdlib only (`hashlib`, `tempfile`, `pathlib`,
  `json`). No third-party code, package, model, or data was
  copied, vendored, or depended on.
- **What was reused:** the Wave 7 `FileSystemSandbox`
  (`core/sandbox_filesystem.py`, own code) for the throwaway copy
  and its resolved-path checks. No new licence obligation
  arises from reusing the app's own module.
- **What was NOT built:** Monte Carlo, counterfactual, and
  digital-twin forms (all stay STUDY). No model, dataset, or
  external service is involved in the prediction, so no
  per-model or per-feed licence read is owed by this slice.
- **Verdict for this slice:** no licence blocker. A future slice
  that adds a model, a dataset, or a live feed must get its own
  licence read BEFORE it is wired, under the standing rule.
