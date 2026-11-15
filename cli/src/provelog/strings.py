"""The CLI's language pack: every user-visible sentence, one flat
dictionary, English only.

The CLI speaks one language, like git/npm/pip (no interface-language
switch); the browser pages (chatview, the verify page) keep their own
bilingual dictionaries and never use this pack. Code never formats prose
itself; it calls t("area.key", **values) and the pack owns the words, the
punctuation and the layout padding.

What stays OUT of the pack, deliberately:
  - the verdict vocabulary after RESULT: (VERIFIED, TAMPERING DETECTED,
    NOT A BUNDLE, ...) - scripts gate on these bytes, so they are fixed;
  - command examples (`provelog verify ...`) - a command is a command;
  - code comments - English only, they are not user-visible.

Comments in this file are never load-bearing: a comment-stripping release
pipeline may remove them (tokenize-based stripping also leaves '#' inside
these values alone - those are prose, not comments).
"""

from __future__ import annotations

                                                                    
STRINGS: dict[str, str] = {
        "meta.description": "ProveLog records AI-agent sessions locally and creates verifiable evidence.",
        "meta.help_groups": (
            "Commands:\n"
            "  setup       Set up automatic recording\n"
            "  status      Check status\n"
            "  doctor      Check recording and verification health\n"
            "  browse      Browse recorded conversations\n"
            "  export      Export an evidence pack\n"
            "  verify      Verify an evidence pack\n"
            "  stop        Pause recording\n"
            "  resume      Resume recording\n"
            "  uninstall   Remove background service files and keep existing evidence"),
        "meta.require_server_extra": (
            "`provelog {command}` is missing its runtime component"
            "{missing_clause}. Reinstall the package:\n"
            "\n"
            "    pip install --upgrade provelog\n"),
        "meta.extra_command": (
            "`provelog {command}` is missing its runtime component. Reinstall:\n"
            "\n"
            "    pip install --upgrade provelog\n"),
                                                                             
        "welcome.text": (
            "ProveLog records AI-agent sessions locally and creates verifiable evidence.\n"
            "\n"
            "Get started:\n"
            "  provelog setup                  Set up automatic recording\n"
            "  provelog browse                   View records\n"
            "  provelog status                 Check status\n"
            "\n"
            "Export and verify:\n"
            "  provelog export\n"
            "  provelog verify my-evidence.zip\n"
            "\n"
            "Help: provelog --help\n"
            "Guide: https://provelog.ai/install"),
                                                                             
        "doctor.help": "check keys, store, chains, stamps and the collector",
        "doctor.offline": "skip the network TSA reachability check",
        "verify.help": "verify a bundle (trusts nobody)",
        "verify.help_tsa_root": ("trust an extra TSA root: HOST is a substring of "
                                 "the TSA URL, CERT.pem the root certificate. "
                                 "Repeatable."),
        "verify.exit_codes": (
            "exit codes:\n"
            "  0  VERIFIED: integrity and time are proven (attribution may be absent "
            "by default).\n"
            "  1  a verdict, and it is not a pass: TAMPERING DETECTED, NO RECORDS, or "
            "NOT A PROVELOG BUNDLE.\n"
            "  2  no verdict: the file is missing, unreadable, or not JSON; or the "
            "arguments were wrong. Prints RESULT: NOT A BUNDLE.\n"
            "  3  a device signature is present but invalid; with --strict, a missing "
            "signature also returns 3.\n"
            "  4  time is not established (absent, not from a recognized TSA, or "
            "invalid): RESULT: INVALID.\n"
            "  5  no record is covered by the timestamp; with --strict, coverage is "
            "not complete.\n"
            "  6  this bundle needs a newer verifier (verifier_min); it is neither "
            "VERIFIED nor TAMPERING.\n"
            "A pack with a valid timestamp but no device signature passes by default "
            "(exit 0);\n--strict requires the signature.\n"
            "Treat 2 as \"check the path you passed\", and only 1 as a finding "
            "about the record."),
                                                                  
        "verify.sp_description": "Verify a ProveLog bundle.",
        "verify.sp_bundle": ("path to an evidence pack (ZIP), a collection ZIP, "
                             "or a bundle JSON; '-' reads stdin"),
        "verify.sp_json": "emit machine-readable JSON",
        "verify.sp_strict": ("fail unless all four axes pass (exit 4 time, "
                             "5 coverage, 3 device signature)"),
        "verify.sp_tsa_root": ("trust an extra TSA root: HOST is a substring of "
                               "the TSA URL, CERT.pem the root certificate. "
                               "Repeatable. Lets you verify timestamps from a "
                               "TSA ProveLog does not bundle."),
                                                                              
        "verify.err_tsa_root_fmt": "--tsa-root expects HOST=CERT.pem, got {value!r}",
                                                                               
                                                                       
                                                                    
                         
        "fact.head": ("Offline verification: all {n} records in \"{file}\" match "
                      "the hash chain"),
        "fact.local_only": ("; this file carries no third-party timestamp, so "
                            "the record time is self-asserted by this machine, "
                            "and holds only if the local computing environment "
                            "is trusted"),
        "fact.covered_single": ("; the chain root is covered by {k} RFC 3161 "
                                "timestamp(s) from {name} (first stamp {t1}, last "
                                "stamp {t2})"),
        "fact.covered_multi": ("; the chain root is covered by {k} RFC 3161 "
                               "timestamp(s) ({groups}), first stamp {t1} ({n1}), "
                               "last stamp {t2} ({n2})"),
        "fact.detected_single": ("; detected {k} RFC 3161 timestamp(s) from {name} "
                                 "(first stamp {t1}, last stamp {t2})"),
        "fact.detected_multi": ("; detected {k} RFC 3161 timestamp(s) ({groups}), "
                                "first stamp {t1} ({n1}), last stamp {t2} "
                                "({n2})"),
        "fact.group_item": "{name} {k}",
        "fact.group_sep": ", ",
        "fact.tail": ("; {tail} more records arrived after the last stamp and "
                      "are not yet covered by a third-party timestamp"),
        "fact.end_doc": ". See {doc}",
        "fact.unchecked": ("; the timestamp signatures were not checked on this "
                           "page - use the command-line tool for the full check."),
                                                                               
        "setup.complete_title": "ProveLog setup complete.",
        "setup.complete_supported": "Supported agents detected: {names}",
        "setup.complete_unsupported": "Unsupported agents detected: {names} (not recorded in v1)",
        "setup.complete_no_supported": "No supported agents detected.",
        "setup.complete_link": "Supported agents: https://provelog.ai/agents",
        "setup.complete_auto": (
            "ProveLog automatically records new session content from supported agents.\n"
            "No additional setup is needed. Existing history will not be imported."),
        "setup.complete_future": (
            "When you start using a supported agent, ProveLog will automatically\n"
            "record new session content. No additional setup is needed.\n"
            "Existing history will not be imported."),
        "setup.complete_records": "Records directory: {path}",
        "setup.uninstall_collector": "Removed collector service file: {path}",
        "setup.uninstall_viewer": "Removed viewer service file: {path}",
        "setup.uninstall_evidence": "Existing evidence has been preserved.",
        "setup.complete_commands": (
            "Commands:\n"
            "  provelog browse        View records\n"
            "  provelog status      Check status\n"
            "  provelog stop        Pause recording\n"
            "  provelog resume      Resume recording\n"
            "  provelog uninstall   Remove background service files and keep existing evidence"),
        "chat.running": "ProveLog viewer is running.",
        "chat.url": "URL: {url}",
        "chat.records_dir": "Records directory: {path}",
        "chat.foreground_stop": ("Press Ctrl+C to stop the viewer. This does not "
                                 "stop recording."),
        "chat.already": "ProveLog viewer is already running.",
        "chat.stopped": "Viewer stopped.",
        "chat.not_running": "Viewer is not running.",
                                                                                
        "serve.banner_title": ("ProveLog test stamp service, running at {base}"),
        "serve.banner_roots": "  It receives fingerprints only, never records.",
        "serve.banner_verifier": "  Test verifier: {base}/verify",
        "serve.banner_dev": ("For development and testing only. Its stamps are "
                             "local and carry no third-party standing."),
}


def t(key: str, **fmt: object) -> str:
    """One user-visible sentence. English is the CLI's only language, so
    there is exactly one pack and no selection to make.

    KeyError when the key is missing - a missing key is a defect to fix,
    not a condition to paper over. Placeholders ({name}) are filled from
    the keyword arguments, so the pack owns the words, the punctuation
    and the layout padding."""
    try:
        text = STRINGS[key]
    except KeyError:
        raise KeyError(
            f"language pack key {key!r} missing from STRINGS - no fallback "
            f"exists") from None
    return text.format(**fmt) if fmt else text
