The investigation of this signal is complete only when all of these hold:
1. Signal explained: the metric that moved, observed against baseline, the window, and whether it recurs, with figures computed from telemetry.
2. Software version identified: the array's current firmware, the previous firmware and the upgrade date.
3. Code changes reviewed: the commits between the previous and current firmware were listed, the relevant diffs read, and a specific commit is implicated or explicitly ruled out.
4. Root-cause hypothesis with evidence: the root cause names a mechanism and cites log lines, telemetry figures and code, and competing hypotheses were tested and ruled out.
5. Artifacts attached: the evidence files are saved under /workspace/investigations/<signal id>/ and listed in the handoff.
6. Duplicates checked: the issue tracker was searched, and the investigation says which existing issue this duplicates, or that it is new.
7. Clear recommendation: exactly one of escalate_engineering, upgrade_firmware, apply_workaround, replace_hardware or dismiss, filed with file_investigation, with the handoff package saved under /workspace/artifacts/.
