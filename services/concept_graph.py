"""Core Automata Theory concept graph and lightweight concept inference helpers."""

from __future__ import annotations

import re
from typing import Any


CONCEPT_GRAPH: dict[str, dict[str, Any]] = {
    "alphabet": {
        "name": "Alphabet",
        "description": "A finite non-empty set of symbols used to build strings.",
        "prerequisites": [],
        "related": ["strings", "languages"],
        "chapters": ["1"],
        "subchapters": ["alphabet", "symbols"],
        "category": "Foundations",
    },
    "strings": {
        "name": "Strings",
        "description": "Finite sequences of symbols drawn from an alphabet.",
        "prerequisites": ["alphabet"],
        "related": ["languages"],
        "chapters": ["1"],
        "subchapters": ["strings", "concatenation"],
        "category": "Foundations",
    },
    "languages": {
        "name": "Languages",
        "description": "Sets of strings over an alphabet.",
        "prerequisites": ["alphabet", "strings"],
        "related": ["finite_automata", "regular_expressions", "closure_properties"],
        "chapters": ["1", "2"],
        "subchapters": ["languages", "formal languages"],
        "category": "Foundations",
    },
    "finite_automata": {
        "name": "Finite Automata",
        "description": "State-based machines used to recognize regular languages.",
        "prerequisites": ["alphabet", "strings", "languages"],
        "related": ["dfa", "nfa", "regular_expressions"],
        "chapters": ["5"],
        "subchapters": ["finite automata", "state machine", "fsm"],
        "category": "Regular Languages",
    },
    "dfa": {
        "name": "Deterministic Finite Automata",
        "description": "Finite automata with exactly one transition per state-symbol pair.",
        "prerequisites": ["alphabet", "strings", "languages", "finite_automata"],
        "related": ["nfa", "dfa_vs_nfa", "dfa_minimization"],
        "chapters": ["5", "6"],
        "subchapters": ["dfa", "deterministic finite automata"],
        "category": "Regular Languages",
    },
    "nfa": {
        "name": "Nondeterministic Finite Automata",
        "description": "Finite automata that may branch across multiple transitions.",
        "prerequisites": ["alphabet", "strings", "languages", "finite_automata"],
        "related": ["epsilon_nfa", "dfa", "nfa_to_dfa"],
        "chapters": ["6"],
        "subchapters": ["nfa", "nondeterministic finite automata"],
        "category": "Regular Languages",
    },
    "epsilon_nfa": {
        "name": "Epsilon-NFA",
        "description": "An NFA that allows epsilon transitions without consuming input.",
        "prerequisites": ["nfa"],
        "related": ["nfa_to_dfa", "regex_to_nfa"],
        "chapters": ["6", "7"],
        "subchapters": ["epsilon nfa", "epsilon transitions", "e-nfa"],
        "category": "Regular Languages",
    },
    "dfa_vs_nfa": {
        "name": "DFA vs NFA",
        "description": "The conceptual and expressive comparison between deterministic and nondeterministic automata.",
        "prerequisites": ["dfa", "nfa"],
        "related": ["nfa_to_dfa", "finite_automata"],
        "chapters": ["6"],
        "subchapters": ["dfa vs nfa", "equivalence of dfa and nfa"],
        "category": "Regular Languages",
    },
    "nfa_to_dfa": {
        "name": "NFA to DFA Conversion",
        "description": "The subset construction for converting nondeterministic automata into deterministic ones.",
        "prerequisites": ["dfa", "nfa", "epsilon_nfa"],
        "related": ["dfa_vs_nfa", "regex_to_nfa"],
        "chapters": ["6"],
        "subchapters": ["subset construction", "nfa to dfa"],
        "category": "Regular Languages",
    },
    "regular_expressions": {
        "name": "Regular Expressions",
        "description": "Symbolic descriptions for regular languages.",
        "prerequisites": ["alphabet", "strings", "languages"],
        "related": ["regex_to_nfa", "closure_properties", "finite_automata"],
        "chapters": ["7"],
        "subchapters": ["regular expressions", "regex"],
        "category": "Regular Languages",
    },
    "regex_to_nfa": {
        "name": "Regex to NFA",
        "description": "Constructing finite automata from regular expressions.",
        "prerequisites": ["regular_expressions", "nfa", "epsilon_nfa"],
        "related": ["nfa_to_dfa", "finite_automata"],
        "chapters": ["7"],
        "subchapters": ["regex to nfa", "thompson construction"],
        "category": "Regular Languages",
    },
    "dfa_minimization": {
        "name": "DFA Minimization",
        "description": "Reducing a DFA to an equivalent machine with the fewest states.",
        "prerequisites": ["dfa", "languages"],
        "related": ["dfa_vs_nfa", "closure_properties"],
        "chapters": ["8"],
        "subchapters": ["dfa minimization", "minimization"],
        "category": "Regular Languages",
    },
    "closure_properties": {
        "name": "Closure Properties",
        "description": "How language classes behave under operations such as union, intersection, and complement.",
        "prerequisites": ["languages", "regular_expressions", "dfa"],
        "related": ["pumping_lemma", "context_free_grammar"],
        "chapters": ["9", "11"],
        "subchapters": ["closure properties", "union", "intersection", "complement"],
        "category": "Regular Languages",
    },
    "pumping_lemma": {
        "name": "Pumping Lemma",
        "description": "A proof technique for showing that some languages are not regular or not context-free.",
        "prerequisites": ["languages", "finite_automata", "closure_properties"],
        "related": ["context_free_grammar", "decidability"],
        "chapters": ["9", "14"],
        "subchapters": ["pumping lemma"],
        "category": "Proof Techniques",
    },
    "context_free_grammar": {
        "name": "Context-Free Grammar",
        "description": "A grammar formalism used to generate context-free languages.",
        "prerequisites": ["alphabet", "strings", "languages"],
        "related": ["pushdown_automata", "pumping_lemma"],
        "chapters": ["10", "11"],
        "subchapters": ["context free grammar", "cfg", "grammar"],
        "category": "Context-Free Languages",
    },
    "pushdown_automata": {
        "name": "Pushdown Automata",
        "description": "Automata with a stack used to recognize many context-free languages.",
        "prerequisites": ["context_free_grammar", "finite_automata"],
        "related": ["turing_machine", "closure_properties"],
        "chapters": ["12", "13"],
        "subchapters": ["pushdown automata", "pda"],
        "category": "Context-Free Languages",
    },
    "turing_machine": {
        "name": "Turing Machine",
        "description": "A general model of algorithmic computation.",
        "prerequisites": ["finite_automata", "pushdown_automata"],
        "related": ["decidability", "complexity_basics"],
        "chapters": ["16", "17", "18", "19"],
        "subchapters": ["turing machine", "tm", "tape", "decider"],
        "category": "Computation",
    },
    "decidability": {
        "name": "Decidability",
        "description": "Whether a problem can be solved algorithmically for every input.",
        "prerequisites": ["turing_machine"],
        "related": ["pumping_lemma", "complexity_basics"],
        "chapters": ["20", "21", "22", "23", "24", "25"],
        "subchapters": ["decidability", "undecidability", "halting problem"],
        "category": "Computation",
    },
    "complexity_basics": {
        "name": "Complexity Basics",
        "description": "Time and space complexity ideas used to compare problem difficulty.",
        "prerequisites": ["turing_machine", "decidability"],
        "related": ["languages"],
        "chapters": ["27", "28", "29", "30"],
        "subchapters": ["complexity", "big o", "p", "np"],
        "category": "Complexity Theory",
    },
}


_SYNONYM_MAP = {
    "finite state machine": "finite_automata",
    "finite state machines": "finite_automata",
    "fsm": "finite_automata",
    "dfa": "dfa",
    "deterministic finite automata": "dfa",
    "deterministic finite automaton": "dfa",
    "nfa": "nfa",
    "nondeterministic finite automata": "nfa",
    "nondeterministic finite automaton": "nfa",
    "epsilon nfa": "epsilon_nfa",
    "epsilon-nfa": "epsilon_nfa",
    "regular expression": "regular_expressions",
    "regular expressions": "regular_expressions",
    "regex": "regular_expressions",
    "cfg": "context_free_grammar",
    "context free grammar": "context_free_grammar",
    "context-free grammar": "context_free_grammar",
    "pda": "pushdown_automata",
    "pushdown automata": "pushdown_automata",
    "pushdown automaton": "pushdown_automata",
    "tm": "turing_machine",
    "turing machine": "turing_machine",
    "complexity": "complexity_basics",
    "undecidability": "decidability",
}


_KEYWORD_RULES: dict[str, tuple[str, ...]] = {
    "alphabet": ("alphabet", "symbol set"),
    "strings": ("string", "concatenation", "substring"),
    "languages": ("language", "set of strings", "formal language"),
    "finite_automata": ("finite automata", "finite automaton", "state machine", "fsm"),
    "dfa": ("dfa", "deterministic finite automata", "deterministic automaton"),
    "nfa": ("nfa", "nondeterministic finite automata", "nondeterministic automaton"),
    "epsilon_nfa": ("epsilon transition", "epsilon-nfa", "epsilon nfa", "e-nfa"),
    "dfa_vs_nfa": ("equivalent power", "dfa vs nfa", "difference between dfa and nfa"),
    "nfa_to_dfa": ("subset construction", "convert nfa", "nfa to dfa"),
    "regular_expressions": ("regular expression", "regex", "kleene star"),
    "regex_to_nfa": ("thompson", "regex to nfa"),
    "dfa_minimization": ("minimize dfa", "dfa minimization", "equivalent states"),
    "closure_properties": ("closure", "union", "intersection", "complement"),
    "pumping_lemma": ("pumping lemma",),
    "context_free_grammar": ("context-free grammar", "context free grammar", "cfg", "production"),
    "pushdown_automata": ("pushdown automata", "pushdown automaton", "pda", "stack"),
    "turing_machine": ("turing machine", "tape", "head movement", "decider"),
    "decidability": ("decidable", "undecidable", "halting problem"),
    "complexity_basics": ("complexity", "time complexity", "space complexity", "p vs np", "np"),
}


def normalize_concept_label(label: str | None) -> str:
    if not label:
        return ""
    normalized = re.sub(r"[_\-]+", " ", str(label).strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in _SYNONYM_MAP:
        return _SYNONYM_MAP[normalized]

    underscored = normalized.replace(" ", "_")
    if underscored in CONCEPT_GRAPH:
        return underscored
    return ""


def get_concept(concept_id: str | None) -> dict[str, Any] | None:
    normalized = normalize_concept_label(concept_id)
    if normalized:
        return CONCEPT_GRAPH.get(normalized)
    return None


def get_prerequisites(concept_id: str | None) -> list[str]:
    concept = get_concept(concept_id)
    if not concept:
        return []
    return list(concept.get("prerequisites", []))


def get_related_concepts(concept_id: str | None) -> list[str]:
    concept = get_concept(concept_id)
    if not concept:
        return []
    return list(concept.get("related", []))


def get_concepts_for_chapter(chapter_id: str | int | None) -> list[str]:
    if chapter_id is None:
        return []
    chapter_key = str(chapter_id).strip()
    matches: list[str] = []
    for concept_id, payload in CONCEPT_GRAPH.items():
        if chapter_key in payload.get("chapters", []):
            matches.append(concept_id)
    return matches


def get_concepts_for_subchapter(subchapter_id: str | None) -> list[str]:
    if not subchapter_id:
        return []
    lowered = str(subchapter_id).strip().lower()
    matches: list[str] = []
    for concept_id, payload in CONCEPT_GRAPH.items():
        for subchapter_label in payload.get("subchapters", []):
            if lowered == str(subchapter_label).strip().lower():
                matches.append(concept_id)
    if matches:
        return matches
    return infer_concepts_from_text(lowered)


def infer_concepts_from_text(text: str | None) -> list[str]:
    if not text:
        return []

    lowered = str(text).strip().lower()
    normalized_direct = normalize_concept_label(lowered)
    found: list[str] = [normalized_direct] if normalized_direct else []

    for concept_id, keywords in _KEYWORD_RULES.items():
        if any(keyword in lowered for keyword in keywords) and concept_id not in found:
            found.append(concept_id)

    for concept_id, payload in CONCEPT_GRAPH.items():
        if concept_id in found:
            continue
        if payload["name"].lower() in lowered:
            found.append(concept_id)

    return found


def infer_concepts_for_quiz_question(question_text: str, chapter_id: str | int | None = None) -> list[str]:
    explicit = infer_concepts_from_text(question_text)
    if explicit:
        return explicit
    return get_concepts_for_chapter(chapter_id)
