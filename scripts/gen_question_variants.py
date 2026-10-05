#!/usr/bin/env python
"""Ask the configured model for 3 paraphrases per golden question (cached). Paraphrases keep the same oracle."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nsw_sim.assistant.questions import GOLDEN  # noqa: E402
from nsw_sim.config import data_dir  # noqa: E402
from nsw_sim.llm import prompts as P  # noqa: E402
from nsw_sim.llm.client import LLM  # noqa: E402
from nsw_sim.llm.schemas import QuestionVariants  # noqa: E402
from nsw_sim.llm.validate import run_json_role  # noqa: E402

if __name__ == "__main__":
    llm = LLM()
    out = {}
    for q in GOLDEN:
        if q.negative or "{TRACE_REF}" in q.text:
            continue
        res = run_json_role(llm, "paraphrase", P.PARAPHRASE_SYSTEM, q.text, QuestionVariants,
                            lambda m: (m, [] if len(m.variants) >= 3 else ["need 3 variants"], 0), lambda q=q: QuestionVariants(variants=[q.text] * 3), schema_version=P.PROMPT_VERSION)
        out[q.id] = res.obj.variants[:3]
        print(q.id, res.source, "|", out[q.id][0][:90], flush=True)
    (data_dir() / "question_variants.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"wrote {len(out)} questions x 3 variants")
