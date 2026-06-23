# -*- coding: utf-8 -*-
"""
Created on Mon Apr 27 13:16:14 2026

@author: oppna

Investigation-chat flow for a stage-gated food contamination seminar.

Main branches:
1. advance_stage: instructor releases the next portion of investigation data
2. task_question: student asks about task / how to approach the case
3. evidence_question: student asks about scientific literature or released evidence
4. hypothesis_submission: student proposes a hypothesis; assistant evaluates it with
   currently available evidence + hidden gold standard, without revealing the gold standard
5. hidden_info_request: student asks for unreleased/hidden information; assistant refuses

"""

from __future__ import annotations

from typing import Dict, Any, Optional, List, Literal
import uuid
import re
import difflib
from langgraph.graph import StateGraph, END


from ethelflow.agents.reasoning.node_adapter import reasoning_node
from ethelflow.agents.embedding.node_adapter import embedding_node
from ethelflow.agents.search_vectors.node_adapter import search_vectors_node
from ethelflow.agents.retrieve_chunks.node_adapter import retrieve_chunks_node

from ethelflow.investigation.state import InvestigationState, InvestigationIntent 
from ethelflow.investigation.helpers import _extract_user_message, _as_list, \
    _stage_docs_up_to, _stage_docs_exact, _history_to_text, _format_context_block, \
        _format_source_label

#from ethelflow.tutor.tutor_rag import prepare_template_fields, render_template, \
 #   prepare_prompt_list, prepare_history_text, prepare_query_embedding

DOCUMENT_NAME_REGISTRY = {
    "61ca0fd3-2ebd-43d4-b1c4-6e5b3df96862": "Situationsblatt_Tahini_Ausbruch.pdf",
    "5cb00a90-5e7e-4910-8be8-d7451487b695": "EHEC_Abschlussbericht_2011_11_24.pdf",
    "e9b1b8d3-0ff5-4595-9194-d54a7d1b5b94": "wissenschaftliche-bewertung-des-ehec-ausbruchsgeschehens-in-deutschland-im-mai-juni-2011.pdf",
    "ef7b5d33-57aa-4cb1-b1ef-4ac4eb794961": "Literaturrecherche_Leitfaden.pdf",
    "4ca3e7ac-a3b8-4071-a6e3-d8dcca1e8fe1": "10_Integrated_surveillance.pdf",
    "8b4b96eb-0a16-40da-baa7-95f0d9accc02": "12_Crisis_communication.pdf",
    # "ffd49663-fbee-4b95-a900-fc08402cc009",
    # "5ba34cae-8112-41f9-9f5d-e6f492933396",
    # "8cd75694-a69f-4bc3-8e29-78c890f1cd01",
    # "cabc7124-0455-4329-b9ee-916fb9815f15",
    # "2ad62e2f-7ff3-4992-b8db-31517ddcb5ee",
    # "539f7f3d-942f-47b6-8f52-679776a73a22",
    # "15f51137-eafd-4264-a6bc-fbb7233fd75e",
    # "4088903d-a261-4014-9b7d-6306709830e4",
    # "87e38de1-c7ac-4bf2-908d-19843bf0498f",
    # "755d4f85-6fcf-402a-ab5a-6c6667209c22",
    # "75ee5809-8fbb-40cc-b05c-6d08b0604384"
    # Add any other document UUIDs and filenames here...
}

DEFAULT_CASE_ID = "food_contamination_case_01"
DEFAULT_CASE_TITLE = "Food contamination investigation"
DEFAULT_MAX_STAGE = 5

DEFAULT_TASK_DESCRIPTION_DOCUMENT_IDS = [
    "61ca0fd3-2ebd-43d4-b1c4-6e5b3df96862"
]

DEFAULT_PROBLEM_APPROACH_DOCUMENT_IDS = [
    "ef7b5d33-57aa-4cb1-b1ef-4ac4eb794961",
    "4ca3e7ac-a3b8-4071-a6e3-d8dcca1e8fe1",
    "8b4b96eb-0a16-40da-baa7-95f0d9accc02",
    "ffd49663-fbee-4b95-a900-fc08402cc009",
    "5ba34cae-8112-41f9-9f5d-e6f492933396",
    "8cd75694-a69f-4bc3-8e29-78c890f1cd01",
    "cabc7124-0455-4329-b9ee-916fb9815f15",
    "2ad62e2f-7ff3-4992-b8db-31517ddcb5ee",
    "539f7f3d-942f-47b6-8f52-679776a73a22",
    "15f51137-eafd-4264-a6bc-fbb7233fd75e",
    "4088903d-a261-4014-9b7d-6306709830e4",
    "87e38de1-c7ac-4bf2-908d-19843bf0498f",
    "755d4f85-6fcf-402a-ab5a-6c6667209c22",
    "75ee5809-8fbb-40cc-b05c-6d08b0604384"
]

DEFAULT_OLD_RESOLVED_CASE_DOCUMENT_IDS = [
    "5cb00a90-5e7e-4910-8be8-d7451487b695",
    "e9b1b8d3-0ff5-4595-9194-d54a7d1b5b94",
]

DEFAULT_SCIENTIFIC_LITERATURE_DOCUMENT_IDS = [
    "91e96a14-dced-49e0-853a-90dc0d4b97ad"
]

DEFAULT_INVESTIGATION_STAGE_DOCUMENTS = {
    "1": ["d002e102-5196-47e8-a45a-82a3b9a93623"],
    "2": ["20fd6a5a-8a7f-4529-ae39-b678f7fed431"],
    "3": ["6b8d7e84-afca-4e5f-86e1-06189092778f"],
    "4": ["7edc249d-7d63-439a-94d1-5d93eb3998cd"],
    "5": ["34a2bba2-5fe5-43e5-a948-31a700372b93"],
}

DEFAULT_GOLD_STANDARD_DOCUMENT_IDS = [
    "851f570b-a894-4b26-877b-2974a7b8788e"
]

DEFAULT_EXTRACTOR = "file_to_text"
DEFAULT_METHOD = "recursive_char_1000_100_pageaware"
DEFAULT_EMBEDDING_SPACE = "ada3_large"
DEFAULT_TOP_K = 10
#______________________________________________________________________________

def build_initial_state(context: Dict[str, Any]) -> InvestigationState:
    context = dict(context or {})
    
    print("DEBUG build_initial_state raw context keys =", list((context or {}).keys()), flush=True)
    print("DEBUG build_initial_state raw context =", context, flush=True)

    # /v1/responses often puts custom app fields inside context["metadata"].
    # The investigation flow expects them as top-level context keys.
    metadata = context.get("metadata")
    if isinstance(metadata, dict):
        merged = dict(metadata)
        merged.update({k: v for k, v in context.items() if k != "metadata"})
        context = merged
        
        
    tenant = context.get("tenant")
    if not isinstance(tenant, str) or not tenant.strip():
        raise ValueError("Missing or invalid 'tenant' in context")

    user_message = _extract_user_message(context)
    history = context.get("history") if isinstance(context.get("history"), list) else []

    rag = context.get("rag") if isinstance(context.get("rag"), dict) else {}

    current_stage = int(context.get("current_stage", context.get("investigation_stage", 0)))
    max_stage = int(context.get("max_stage", 10))

    # Document categories. These are expected to be lists of Ethel document UUIDs.
    # task_docs = _as_list(context.get("task_description_document_ids", context.get("task_description_docs", [])))
    # approach_docs = _as_list(context.get("problem_approach_document_ids", context.get("problem_approach_docs", [])))
    # old_case_docs = _as_list(context.get("old_resolved_case_document_ids", context.get("old_contamination_case_document_ids", [])))
    # literature_docs = _as_list(context.get("scientific_literature_document_ids", context.get("literature_document_ids", [])))
    # gold_docs = _as_list(context.get("gold_standard_document_ids", context.get("answer_key_document_ids", [])))
    
    task_docs = _as_list(
         context.get("task_description_document_ids")
         or rag.get("task_description_document_ids")
         or context.get("task_description_docs")
         or DEFAULT_TASK_DESCRIPTION_DOCUMENT_IDS
     )
     
    approach_docs = _as_list(
        context.get("problem_approach_document_ids")
        or rag.get("problem_approach_document_ids")
        or context.get("problem_approach_docs")
        or DEFAULT_PROBLEM_APPROACH_DOCUMENT_IDS
    )
     
    old_case_docs = _as_list(
        context.get("old_resolved_case_document_ids")
        or rag.get("old_resolved_case_document_ids")
        or context.get("old_contamination_case_document_ids")
        or DEFAULT_OLD_RESOLVED_CASE_DOCUMENT_IDS
    )
     
    literature_docs = _as_list(
        context.get("scientific_literature_document_ids")
        or rag.get("scientific_literature_document_ids")
        or context.get("literature_document_ids")
        or DEFAULT_SCIENTIFIC_LITERATURE_DOCUMENT_IDS
    )
     
    gold_docs = _as_list(
        context.get("gold_standard_document_ids")
        or rag.get("gold_standard_document_ids")
        or context.get("answer_key_document_ids")
        or DEFAULT_GOLD_STANDARD_DOCUMENT_IDS
    )

    # investigation_stage_documents = context.get(
    #     "investigation_stage_documents",
    #     context.get("investigation_data_by_stage", {}),
    # )
    investigation_stage_documents = (
        context.get("investigation_stage_documents")
        or rag.get("investigation_stage_documents")
        or context.get("investigation_data_by_stage")
        or DEFAULT_INVESTIGATION_STAGE_DOCUMENTS
    )

    # Fallback for older contexts: if only document_ids are supplied, use them as student-visible docs.
    fallback_docs = _as_list(rag.get("document_ids", context.get("document_ids", [])))

    state = InvestigationState(
        tenant=tenant.strip(),
        # case_id=context.get("case_id", "default_case"),
        # case_title=context.get("case_title", "Food contamination investigation"),
        # current_stage=int(
        #     context.get("current_stage")
        #     or rag.get("current_stage")
        #     or context.get("investigation_stage")
        #     or 0
        # ),#current_stage,
        # max_stage=max_stage,
        
        #case_id=context.get("case_id") or rag.get("case_id") or "default_case",
        #case_title=context.get("case_title") or rag.get("case_title") or "Food contamination investigation",
        
        case_id=context.get("case_id") or rag.get("case_id") or DEFAULT_CASE_ID,
        case_title=context.get("case_title") or rag.get("case_title") or DEFAULT_CASE_TITLE,

        #max_stage=int(context.get("max_stage") or rag.get("max_stage") or 10),
        
        current_stage = int(
            context.get("current_stage")
            or rag.get("current_stage")
            or context.get("investigation_stage")
            or 0
        ),
        
        max_stage = int(
            context.get("max_stage")
            or rag.get("max_stage")
            or DEFAULT_MAX_STAGE
        ),

        last_user_msg=user_message,
        history=history,
        history_text="",
        router_prompt="",
        router_raw_output="",
        intent="general_chat",
        response_type="general_answer",
        retrieval_scope="student_visible",
        task_description_document_ids=task_docs,
        problem_approach_document_ids=approach_docs,
        old_resolved_case_document_ids=old_case_docs,
        scientific_literature_document_ids=literature_docs,
        investigation_stage_documents=investigation_stage_documents,
        investigation_data_document_ids=_stage_docs_up_to(investigation_stage_documents, current_stage),
        gold_standard_document_ids=gold_docs,
        allowed_document_ids=[],
        blocked_document_ids=[],
        newly_released_document_ids=[],
        fallback_document_ids=fallback_docs,
        # extractor=rag.get("extractor", context.get("extractor", "file_to_text")),
        # method=rag.get("method", context.get("method", "recursive_char_1000_100_pageaware")),
        # embedding_space=rag.get("embedding_space", context.get("embedding_space")),
        # top_k=int(rag.get("top_k", context.get("top_k", 10))),
        
        extractor=rag.get("extractor") or context.get("extractor") or DEFAULT_EXTRACTOR,
        method=rag.get("method") or context.get("method") or DEFAULT_METHOD,
        embedding_space=rag.get("embedding_space") or context.get("embedding_space") or DEFAULT_EMBEDDING_SPACE,
        top_k=int(rag.get("top_k") or context.get("top_k") or DEFAULT_TOP_K),

        prompts=[],
        embeddings=[],
        query_embedding=[],
        search_vectors_response={},
        hit_chunk_ids=[],
        retrieve_chunks_response={},
        chunk_texts=[],
        chunk_metadata=[],
        source_labels=[],
        gold_prompts=[],
        gold_embeddings=[],
        gold_query_embedding=[],
        gold_search_vectors_response={},
        gold_hit_chunk_ids=[],
        gold_retrieve_chunks_response={},
        gold_chunk_texts=[],
        gold_chunk_metadata=[],
        final_prompt="",
        reasoning_raw_output="",
        submitted_hypothesis=None,
        hypothesis_review={},
        draft_response=context.get("draft_response", ""),
        debug={},
    )
    
    print("DEBUG initial state case_id =", state.get("case_id"), flush=True)
    print("DEBUG initial state current_stage =", state.get("current_stage"), flush=True)
    print("DEBUG initial state task docs =", state.get("task_description_document_ids"), flush=True)
    print("DEBUG initial state approach docs =", state.get("problem_approach_document_ids"), flush=True)
    print("DEBUG initial state old case docs =", state.get("old_resolved_case_document_ids"), flush=True)
    print("DEBUG initial state literature docs =", state.get("scientific_literature_document_ids"), flush=True)
    print("DEBUG initial state investigation_stage_documents =", state.get("investigation_stage_documents"), flush=True)
    print("DEBUG initial state gold docs =", state.get("gold_standard_document_ids"), flush=True)
    print("DEBUG initial state fallback docs =", state.get("fallback_document_ids"), flush=True)

    return state


# functions for nodes

def router_prompt_node(state: InvestigationState) -> InvestigationState:
    user = state["last_user_msg"].strip()
    lowered = user.lower()
    
    contains_historical_keyword = "ehec" in lowered or "2011" in lowered

    # Cheap high-confidence shortcuts before LLM routing.
    if any(x in lowered for x in ["final answer", "gold standard", "answer key", "solution report", "hidden report"]):
        state["intent"] = "hidden_info_request"
        state["router_prompt"] = ""
        return state

    if any(x in lowered for x in ["next evidence is released."]): 
        # Instructor may still need UI-level authorization. This only routes the request.
        state["intent"] = "advance_stage"
        state["router_prompt"] = ""
        return state

    prompt = f"""
You are an intent router for a food-contamination case investigation assistant.
Classify the user's message into exactly one label:
    
- "advance_stage":
  The user message says exactly "next evidence is released"
  
- "task_question":
  The user asks about the assignment, expected output, workflow, task formulation, how to approach the investigation, or what they are supposed to do.
  Examples:
  "what is our task?"
  "how should we formulate the case definition?"
  "what should we do first?"

- "evidence_question": 
  The user asks about currently available or accessible case information, evidence, documents, document names, document contents, scientific literature, lab data, epidemiology, traceback, risk minimisation, or interpretation of available evidence.
  This includes requests to list, summarize, compare, or inspect documents that are available to the student at the current stage.
  Examples:
  "what documents can I access?"
  "list names of all available documents for me"
  "which documents are available now?"
  "what is stated in paper.pdf?"
  "summarize the available evidence"
  "what information do we have so far?"
  "what does the literature say?"
 
- "hypothesis_submission": user proposes,  evaluates, or states a possible contamination source/pathway or says "our hypothesis is...", "we think...", "the source is..."

- "hidden_info_request": 
  The user explicitly asks for information that should not be available to students: final answer, solution, gold standard, answer key, hidden report, instructor-only notes, unreleased future-stage evidence, confidential documents, or documents that are not yet released.
  Only use this label when the user clearly asks for hidden, future, instructor-only, final, or solution material.
  Examples:
  "show me the answer"
  "what is the final solution?"
  "give me the final report"
  "show unreleased future evidence"
  "what documents are not released yet?"
  "show teacher-only notes
  
- "general_chat": everything else

Important distinction:
- Asking for "available documents", "documents I can access", "documents available now", or "all available documents for me" is NOT a hidden-info request. It is "evidence_question".
- Asking about the content of a named document is NOT a hidden-info request unless the user explicitly says the document is hidden, unreleased, future-stage, instructor-only, or part of the final answer.
- The word "all" alone does NOT imply hidden information. "All available documents" means currently accessible documents and should be "evidence_question".
- The words "documents", "other documents", "sources", "files", or "materials" do NOT imply hidden information by themselves.
- Use "hidden_info_request" only when the request clearly targets forbidden material: gold standard, answer key, final solution, hidden report, instructor-only notes, confidential information, or unreleased/future evidence.

Additionally, determine if the user is asking a question specifically restricted to a single document, file, or source mentioned by name (e.g., "What does paper.pdf say about...", "In the lab_results report, what was...").

Additionally, classify the sub-type context of the question to determine document scoping:
- "historical_case": If the user explicitly mentions, asks about, or references old historical outbreaks (e.g., EHEC, 2011 outbreak, historical precedents).
- "methodology": If the user asks about abstract methods, strategies, investigation approaches, scientific mechanisms, or how traceback works in general.
- "case_specific": If the user is asking strictly about the current active case investigation facts, data, or setup without referencing general methodologies or past history.

Return your response in exactly this format:
Intent: <label>
Sub-Type: <historical_case | methodology | case_specific>
Target Document: <extracted file name or None>

Message:
{user}
""".strip()
    state["router_prompt"] = prompt
    return state

   
def router_label_node(state: InvestigationState) -> InvestigationState:
    raw_output = (state.get("router_raw_output") or "").strip()
    
    user_msg_lowered = (state.get("last_user_msg") or "").lower()
    
    intent = "general_chat"
    sub_type = "case_specific"
    target_doc = None

    # Simple line parsing
    for line in raw_output.splitlines():
        if line.lower().startswith("intent:"):
            intent = line.split(":", 1)[1].strip().lower()
        elif line.lower().startswith("sub-type:"):
            sub_type = line.split(":", 1)[1].strip().lower()
        elif line.lower().startswith("target document:"):
            doc_val = line.split(":", 1)[1].strip()
            if doc_val.lower() != "none":
                target_doc = doc_val

    if "ehec" in user_msg_lowered or "2011" in user_msg_lowered:
        sub_type = "historical_case"
        
    allowed_intents = {"advance_stage", "task_question", "evidence_question", "hypothesis_submission", "hidden_info_request", "general_chat"}
    if intent not in allowed_intents:
        intent = "general_chat"

    state["intent"] = intent
    state["question_sub_type"] = sub_type
    state["target_document_filter"] = target_doc
    return state

def access_policy_node(state: InvestigationState) -> InvestigationState:
    """Build allowed_document_ids for this turn, applying fuzzy document-specific filters if requested."""
    current_stage = int(state.get("current_stage", 0))
    intent = state.get("intent", "general_chat")
    sub_type = state.get("question_sub_type", "case_specific")
    target_filter = state.get("target_document_filter") 

    task_docs = _as_list(state.get("task_description_document_ids", []))
    approach_docs = _as_list(state.get("problem_approach_document_ids", []))
    old_case_docs = _as_list(state.get("old_resolved_case_document_ids", []))
    literature_docs = _as_list(state.get("scientific_literature_document_ids", []))
    stage_docs = _stage_docs_up_to(state.get("investigation_stage_documents", {}), current_stage)
    gold_docs = _as_list(state.get("gold_standard_document_ids", []))

    # Determine baseline allowed scope based on intent
    allowed = []
    if intent == "task_question":
        allowed = task_docs + approach_docs
        state["retrieval_scope"] = "task_only"
    elif intent in {"evidence_question", "general_chat"}:
        if sub_type == "historical_case":
            allowed = approach_docs + literature_docs + old_case_docs
        elif sub_type == "methodology":
            allowed = approach_docs + literature_docs + task_docs + stage_docs
        else:
            allowed = task_docs + stage_docs
        state["retrieval_scope"] = "student_visible"
    elif intent == "hypothesis_submission":
        allowed = task_docs + stage_docs + gold_docs
        state["retrieval_scope"] = "evaluation"
    else:
        allowed = []
        state["retrieval_scope"] = "none"

    # Strict Gatekeeping: Never allow the gold standard in student-visible searches
    allowed = [doc for doc in allowed if doc not in set(gold_docs)]

    # Preserve baseline allowed items in case filtering yields no registered matches
    baseline_allowed = list(allowed)

    # --- APPLY FUZZY SPECIFIC DOCUMENT FILTER ---
    if target_filter and allowed:
        target_clean = target_filter.strip().lower()
        matched_doc_ids = []

        # 1. First Pass: Check for direct substring matching against the unlocked files
        for doc_id in allowed:
            doc_name = DOCUMENT_NAME_REGISTRY.get(doc_id, "").lower()
            if doc_name and (target_clean in doc_name or doc_name in target_clean):
                matched_doc_ids.append(doc_id)

        # 2. Second Pass: Fuzzy string matching
        if not matched_doc_ids:
            current_name_to_id = {
                DOCUMENT_NAME_REGISTRY[doc_id].lower(): doc_id 
                for doc_id in allowed if doc_id in DOCUMENT_NAME_REGISTRY
            }
            
            closest_names = difflib.get_close_matches(
                target_clean, current_name_to_id.keys(), n=1, cutoff=0.4
            )
            if closest_names:
                matched_doc_ids.append(current_name_to_id[closest_names[0]])

        # If we successfully resolved the user's text to a registered document, isolate it
        if matched_doc_ids:
            allowed = matched_doc_ids
            print(f"DEBUG: Restricted search strictly to document IDs: {allowed}", flush=True)
        else:
            # IMPROVEMENT FALLBACK: If the file name is unregistered (e.g. dynamic stage docs),
            # retain all currently unlocked stage/task documents so the vector search engine
            # can locate the information semantically via text chunk matching.
            allowed = baseline_allowed
            print(f"DEBUG: Target filter '{target_filter}' did not match registry. Falling back to full allowed stage scope.", flush=True)

    # Deduplicate while preserving order
    seen = set()
    allowed_unique = []
    for doc in allowed:
        if doc not in seen:
            seen.add(doc)
            allowed_unique.append(doc)

    state["allowed_document_ids"] = allowed_unique
    state["investigation_data_document_ids"] = stage_docs
    state["blocked_document_ids"] = gold_docs + _stage_docs_exact(state.get("investigation_stage_documents", {}), current_stage + 1)
    
    return state
    
def stage_update_node(state: InvestigationState) -> InvestigationState:
    #user = state.get("last_user_msg", "")
    current_stage = int(state.get("current_stage", 0))
    max_stage = int(state.get("max_stage", 10))

    #requested_stage: Optional[int] = None
    # match = re.search(r"stage\s*(\d+)|step\s*(\d+)", user.lower())
    # if match:
    #     requested_stage = int(next(g for g in match.groups() if g is not None))

    #new_stage = requested_stage if requested_stage is not None else current_stage + 1
    new_stage = current_stage + 1
    new_stage = max(0, min(new_stage, max_stage))
    
    print("DEBUG stage_update current_stage before =", current_stage, flush=True)
    print("DEBUG stage_update max_stage =", max_stage, flush=True)
    print("DEBUG stage_update new_stage =", new_stage, flush=True)
    print("DEBUG stage_update investigation_stage_documents =", state.get("investigation_stage_documents"), flush=True)


    newly_released = _stage_docs_exact(state.get("investigation_stage_documents", {}), new_stage)
    state["current_stage"] = new_stage
    state["newly_released_document_ids"] = newly_released
    print("DEBUG newly released doc ids = ", newly_released, flush=True)
    state["investigation_data_document_ids"] = _stage_docs_up_to(
        state.get("investigation_stage_documents", {}), new_stage
    )
    state["response_type"] = "stage_update"

    if newly_released:
        state["draft_response"] = (
            f"Investigation stage {new_stage} is now active. "
            f"A new portion of case evidence is available. "
            f"You can now revisit your hypotheses using the newly released data."
        )
    else:
        state["draft_response"] = (
            f"Investigation stage {new_stage} is now active. "
            f"No additional document IDs were registered for this stage."
        )
    return state


def refusal_node(state: InvestigationState) -> InvestigationState:
    state["response_type"] = "access_refusal"
    state["draft_response"] = (
        "I cannot provide hidden, future, or final-solution information. "
        "I can help you reason from the evidence that is currently available. "
        "A useful next step is to state your current hypothesis and identify which available evidence supports or contradicts it."
    )
    return state


def prepare_student_query_node(state: InvestigationState) -> InvestigationState:
    state["prompts"] = [state.get("last_user_msg", "")]
    return state


def set_query_embedding_node(state: InvestigationState) -> InvestigationState:
    embeddings = state.get("embeddings") or []
    state["query_embedding"] = embeddings[0] if embeddings else []
    return state


def prepare_gold_query_node(state: InvestigationState) -> InvestigationState:
    state["gold_prompts"] = [
        "Evaluate this student hypothesis against the gold standard answer: "
        + str(state.get("submitted_hypothesis") or state.get("last_user_msg") or "")
    ]
    return state


def set_gold_query_embedding_node(state: InvestigationState) -> InvestigationState:
    embeddings = state.get("gold_embeddings") or []
    state["gold_query_embedding"] = embeddings[0] if embeddings else []
    return state


def build_answer_prompt_node(state: InvestigationState) -> InvestigationState:
    history_text = _history_to_text(state.get("history", []))
    state["history_text"] = history_text
    context_block = _format_context_block(state.get("chunk_texts", []), state.get("chunk_metadata", []))
    source_labels = [
        _format_source_label(meta) for meta in state.get("chunk_metadata", []) if isinstance(meta, dict)
    ]
    state["source_labels"] = source_labels

    intent = state.get("intent", "general_chat")
    user = state.get("last_user_msg", "")
    current_stage = state.get("current_stage", 0)
    
    # Convert the list of labels into a readable string block
    labels_block = "\n".join([f"Source {i+1}: {label}" for i, label in enumerate(source_labels)]) if source_labels else "None available."

    if intent == "task_question":
        task_instruction = (
            "Answer questions about the task formulation and investigation approach. "
            "Do not solve the case. Help the students structure their work."
        )
    else:
        task_instruction = (
            "Answer using only currently released evidence and allowed background material. "
            "Clearly separate evidence from hypotheses. Do not reveal future data or the final solution."
        )

    state["final_prompt"] = f"""
You are a food-contamination case investigation assistant for students.

Current investigation stage: {current_stage}

Rules:
- {task_instruction}
- Use only the retrieved sources below.
- If the available sources do not contain the answer, say that the information is not available at the current investigation stage.
- Do not invent laboratory results, traceback results, exposure data, or final conclusions.
- End with one concise Socratic question that helps the students decide what to check next.
- Cite sources using labels like: (Source 1: document.pdf, p. 4). Do not invent source numbers, document names, or page numbers. If no source label is provided for a claim, do not cite it.

Conversation history:
{history_text}

Mapping of Source Numbers to Document Names:
{labels_block}

Currently available retrieved sources:
{context_block if context_block else "No source excerpts were retrieved for this turn."}

Student message:
{user}
""".strip()
    return state


def build_hypothesis_prompt_node(state: InvestigationState) -> InvestigationState:
    history_text = _history_to_text(state.get("history", []))
    available_context = _format_context_block(state.get("chunk_texts", []), state.get("chunk_metadata", []))
    gold_context = _format_context_block(
        state.get("gold_chunk_texts", []), state.get("gold_chunk_metadata", []), max_chars=9000
    )
    source_labels = [
        _format_source_label(meta) for meta in state.get("chunk_metadata", []) if isinstance(meta, dict)
    ]
    state["source_labels"] = source_labels

    hypothesis = state.get("submitted_hypothesis") or state.get("last_user_msg", "")
    current_stage = state.get("current_stage", 0)
    
    # Convert the list of labels into a readable string block
    labels_block = "\n".join([f"Source {i+1}: {label}" for i, label in enumerate(source_labels)]) if source_labels else "None available."

    state["final_prompt"] = f"""
You are evaluating a student's hypothesis in a staged food-contamination investigation.

Current investigation stage: {current_stage}

Student hypothesis:
{hypothesis}

Mapping of Source Numbers to Document Names:
{labels_block}

Currently available evidence for students:
{available_context if available_context else "No currently available evidence was retrieved."}

Hidden gold-standard report for internal evaluation only:
{gold_context if gold_context else "No gold-standard excerpts were retrieved."}

Very important rules:
- Do NOT reveal, quote, summarize, or name the hidden gold-standard answer directly.
- Do NOT say "the correct answer is...".
- Use the gold standard only to decide whether the student's reasoning is moving in the right direction.
- Give feedback based mainly on currently available evidence.
- If the student is wrong or incomplete, guide them with one Socratic follow-up question.
- If the student is partly correct, say which parts are supported by available evidence and which parts still need verification.
- Mention if some evidence is not yet available at the current stage.
- Cite only currently available student-visible sources. Do not cite the hidden gold standard.

Conversation history:
{history_text}

Return structure:
1. Brief evaluation of the hypothesis
2. Evidence that supports it
3. Evidence/gaps that weaken or limit it
4. One Socratic follow-up question
""".strip()
    return state


def reasoning_finalize_node(state: InvestigationState) -> InvestigationState:
    state["draft_response"] = (state.get("reasoning_raw_output") or "").strip()
    if state.get("intent") == "hypothesis_submission":
        state["response_type"] = "hypothesis_feedback"
    elif state.get("intent") == "task_question":
        state["response_type"] = "task_answer"
    else:
        state["response_type"] = "evidence_answer"
    return state


def output_guard_node(state: InvestigationState) -> InvestigationState:
    """Light final guard. Real protection is retrieval filtering; this is an extra safety net."""
    text = state.get("draft_response", "") or ""
    suspicious = [
        "gold standard report says",
        "hidden report says",
        "answer key says",
        "the final answer is",
        "the correct source is",
    ]
    if any(s in text.lower() for s in suspicious):
        state["draft_response"] = (
            "I cannot reveal hidden solution information. "
            "Based on the currently available evidence, please compare your hypothesis with the exposure pattern, laboratory findings, and traceback information that have been released so far. "
            "Which piece of currently available evidence most strongly supports your hypothesis, and which piece could contradict it?"
        )
        state["response_type"] = "access_refusal"
    return state

def formatter_node(state: InvestigationState) -> InvestigationState:
    headers = {
        "stage_update": "🧩 Investigation stage updated",
        "task_answer": "📋 Task guidance",
        "evidence_answer": "🔎 Investigation support",
        "hypothesis_feedback": "🧠 Hypothesis feedback",
        "access_refusal": "🔒 Access limited",
        "general_answer": "💬 Investigation chat",
    }
    header = headers.get(state.get("response_type", ""), "🔎 Investigation support")
    body = state.get("draft_response", "").strip()
    state["draft_response"] = f"## {header}\n\n{body}".strip()
    return state


def route_after_router_prompt(state: InvestigationState) -> Literal["router_reasoning", "router_label"]:
    # If a shortcut already set intent and no prompt is needed, skip LLM router.
    return "router_label" if not state.get("router_prompt") else "router_reasoning"


def route_by_intent(state: InvestigationState) -> Literal[
    "stage_update",
    "refusal",
    "prepare_student_query",
]:
    intent = state.get("intent", "general_chat")
    if intent == "advance_stage":
        return "stage_update"
    if intent == "hidden_info_request":
        return "refusal"
    return "prepare_student_query"


def route_student_retrieval(state: InvestigationState) -> Literal["embedding", "build_answer_prompt", "prepare_gold_query"]:
    docs = state.get("allowed_document_ids") or []
    if docs:
        return "embedding"
    if state.get("intent") == "hypothesis_submission":
        return "prepare_gold_query"
    return "build_answer_prompt"


def route_after_student_retrieval(state: InvestigationState) -> Literal["prepare_gold_query", "build_answer_prompt"]:
    if state.get("intent") == "hypothesis_submission":
        return "prepare_gold_query"
    return "build_answer_prompt"

def route_gold_retrieval(state: InvestigationState) -> Literal["gold_embedding", "build_hypothesis_prompt"]:
    docs = state.get("gold_standard_document_ids") or []
    return "gold_embedding" if docs else "build_hypothesis_prompt"



async def run(
    thread_id: uuid.UUID,
    context=None,
    stream: bool = False,
    checkpointer=None,
    command=None,
):
    """
    Flow: investigator_chat

    Expected context fields include:
    - tenant
    - user_message / input / messages
    - case_id, case_title
    - current_stage, max_stage
    - task_description_document_ids
    - problem_approach_document_ids
    - old_resolved_case_document_ids
    - scientific_literature_document_ids
    - investigation_stage_documents, e.g. {"1": [doc_id], "2": [doc_id]}
    - gold_standard_document_ids
    - rag: {extractor, method, embedding_space, top_k}
    """

    context = context or {}
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")

    initial_state = build_initial_state(context)
    print("DEBUG initial_state last_user_msg =", initial_state.get("last_user_msg"), flush=True)
    user_text = initial_state["last_user_msg"]  

    config = {"configurable": {"thread_id": str(thread_id)}}
    
    history = list(initial_state.get("history", []))
    history.append({"role": "user", "content": user_text})
    initial_state["history"] = history


    workflow = StateGraph(InvestigationState)

    workflow.add_node("router_prompt", router_prompt_node)
    workflow.add_node(
        "router_reasoning",
        reasoning_node(
            tenant_key="tenant",
            prompt_key="router_prompt",
            reasoning_effort_key=None, #"reasoning_effort",
            stream_key=None,  # don't stream this node; easier for interrupt parsing
            output_key="router_raw_output",
        ),
    )
    workflow.add_node("router_label", router_label_node)
    workflow.add_node("access_policy", access_policy_node)
    workflow.add_node("stage_update", stage_update_node)
    workflow.add_node("refusal", refusal_node)

    workflow.add_node("prepare_student_query", prepare_student_query_node)
    workflow.add_node(
        "embedding",
        embedding_node(
            input_texts_key="prompts",
            tenant_key="tenant",
            space_key="embedding_space",
            output_key="embeddings",
        ),
    )
    
    workflow.add_node("set_query_embedding", set_query_embedding_node)
    workflow.add_node(
        "search_vectors",
        search_vectors_node(
            document_ids_key="allowed_document_ids",
            extractor_key="extractor",
            method_key="method",
            tenant_key="tenant",
            space_key="embedding_space",
            query_vector_key="query_embedding",
            top_k_key="top_k",
            output_key="search_vectors_response",
            output_chunk_ids_key="hit_chunk_ids",
        ),
    )

    workflow.add_node(
        "retrieve_chunks",
        retrieve_chunks_node(
            chunk_ids_key="hit_chunk_ids",
            tenant_key="tenant",
            output_key="retrieve_chunks_response",
            output_texts_key="chunk_texts",
            output_metadata_key="chunk_metadata",
        ),
    )
    
    workflow.add_node("prepare_gold_query", prepare_gold_query_node)
    workflow.add_node(
        "gold_embedding",
        embedding_node(
            input_texts_key="gold_prompts",
            tenant_key="tenant",
            space_key="embedding_space",
            output_key="gold_embeddings",
        ),
    )
    workflow.add_node("set_gold_query_embedding", set_gold_query_embedding_node)
    workflow.add_node(
        "gold_search_vectors",
        search_vectors_node(
            document_ids_key="gold_standard_document_ids",
            extractor_key="extractor",
            method_key="method",
            tenant_key="tenant",
            space_key="embedding_space",
            query_vector_key="gold_query_embedding",
            top_k_key="top_k",
            output_key="gold_search_vectors_response",
            output_chunk_ids_key="gold_hit_chunk_ids",
        ),
    )
    workflow.add_node(
        "gold_retrieve_chunks",
        retrieve_chunks_node(
            chunk_ids_key="gold_hit_chunk_ids",
            tenant_key="tenant",
            output_key="gold_retrieve_chunks_response",
            output_texts_key="gold_chunk_texts",
            output_metadata_key="gold_chunk_metadata",
        ),
    )

    workflow.add_node("build_answer_prompt", build_answer_prompt_node)
    workflow.add_node("build_hypothesis_prompt", build_hypothesis_prompt_node)
    workflow.add_node(
        "reasoning",
        reasoning_node(
            tenant_key="tenant",
            prompt_key="final_prompt",
            reasoning_effort_key=None,
            stream_key=None,
            output_key="reasoning_raw_output",
        ),
    )

    workflow.add_node("reasoning_finalize", reasoning_finalize_node)
    workflow.add_node("output_guard", output_guard_node)
    workflow.add_node("formatter", formatter_node)
    

    
    workflow.set_entry_point("router_prompt")
    
    workflow.add_conditional_edges(
        "router_prompt",
        route_after_router_prompt,
        {
            "router_reasoning": "router_reasoning",
            "router_label": "router_label",
        },
    )

    workflow.add_edge("router_reasoning", "router_label")
    workflow.add_edge("router_label", "access_policy")
    
    workflow.add_conditional_edges(
        "access_policy",
        route_by_intent,
        {
            "stage_update": "stage_update",
            "refusal": "refusal",
            "prepare_student_query": "prepare_student_query",
        },
    )

    workflow.add_conditional_edges(
        "prepare_student_query",
        route_student_retrieval,
        {
            "embedding": "embedding",
            "build_answer_prompt": "build_answer_prompt",
            "prepare_gold_query": "prepare_gold_query",
        },
    )
    workflow.add_edge("embedding", "set_query_embedding")
    workflow.add_edge("set_query_embedding", "search_vectors")
    workflow.add_edge("search_vectors", "retrieve_chunks")
    workflow.add_conditional_edges(
        "retrieve_chunks",
        route_after_student_retrieval,
        {
            "prepare_gold_query": "prepare_gold_query",
            "build_answer_prompt": "build_answer_prompt",
        },
    )

    workflow.add_conditional_edges(
        "prepare_gold_query",
        route_gold_retrieval,
        {
            "gold_embedding": "gold_embedding",
            "build_hypothesis_prompt": "build_hypothesis_prompt",
        },
    )
    workflow.add_edge("gold_embedding", "set_gold_query_embedding")
    workflow.add_edge("set_gold_query_embedding", "gold_search_vectors")
    workflow.add_edge("gold_search_vectors", "gold_retrieve_chunks")
    workflow.add_edge("gold_retrieve_chunks", "build_hypothesis_prompt")

    workflow.add_edge("build_answer_prompt", "reasoning")
    workflow.add_edge("build_hypothesis_prompt", "reasoning")
    workflow.add_edge("reasoning", "reasoning_finalize")
    workflow.add_edge("reasoning_finalize", "output_guard")
    workflow.add_edge("output_guard", "formatter")
    workflow.add_edge("stage_update", "formatter")
    workflow.add_edge("refusal", "formatter")
    workflow.add_edge("formatter", END)


    app = workflow.compile(checkpointer=checkpointer)
    
    print("HELLO I AM HERE", flush=True)
    
    try:
        out = await app.ainvoke(initial_state, config=config)
        print("DEBUG post-router response_type =", out.get("response_type"), flush=True)
        
        print("DEBUG out intent =", out.get("intent"), flush=True)
        print("DEBUG out target doc =", out.get("target_document_filter"), flush=True)
        
        print("DEBUG out response_type =", out.get("response_type"), flush=True)
        print("DEBUG out current_stage =", out.get("current_stage"), flush=True)
        print("DEBUG out allowed_document_ids =", out.get("allowed_document_ids"), flush=True)
        print("DEBUG out hit_chunk_ids =", out.get("hit_chunk_ids"), flush=True)
        print("DEBUG out n chunk_texts =", len(out.get("chunk_texts") or []), flush=True)
        print("DEBUG out chunk_metadata =", out.get("chunk_metadata"), flush=True)
        #print("DEBUG out source_labels =", out.get("source_labels"), flush=True)
        #print("DEBUG out search_vectors_response =", out.get("search_vectors_response"), flush=True)
        #print("DEBUG out retrieve_chunks_response =", out.get("retrieve_chunks_response"), flush=True)
    except Exception as e:
        print("DEBUG app.ainvoke EXCEPTION =", repr(e), flush=True)
        print("DEBUG out response_type =", out.get("response_type"), flush=True)
        raise
        
    
    history = list(out.get("history", []))
    history.append({"role": "assistant", "content": out.get("draft_response", "")})
    out["history"] = history
    out["messages"] = history
    
            
    # print("DEBUG out =", out, flush=True)
    yield {
        "answer": out.get("draft_response", ""),
        "response_type": out.get("response_type", ""),
        "intent": out.get("intent", ""),
        "current_stage": out.get("current_stage", 0),
        "context": {
            "tenant": out.get("tenant"),
            "case_id": out.get("case_id"),
            "case_title": out.get("case_title"),
            "current_stage": out.get("current_stage", 0),
            "max_stage": out.get("max_stage", 10),
            "history": out.get("history", []),
            "task_description_document_ids": out.get("task_description_document_ids", []),
            "problem_approach_document_ids": out.get("problem_approach_document_ids", []),
            "old_resolved_case_document_ids": out.get("old_resolved_case_document_ids", []),
            "scientific_literature_document_ids": out.get("scientific_literature_document_ids", []),
            "investigation_stage_documents": out.get("investigation_stage_documents", {}),
            "gold_standard_document_ids": out.get("gold_standard_document_ids", []),
            "allowed_document_ids": out.get("allowed_document_ids", []),
            "blocked_document_ids": out.get("blocked_document_ids", []),
            "newly_released_document_ids": out.get("newly_released_document_ids", []),
            "extractor": out.get("extractor"),
            "method": out.get("method"),
            "embedding_space": out.get("embedding_space"),
            "top_k": out.get("top_k", 10),
            "intent": out.get("intent", ""),
            "response_type": out.get("response_type", ""),
            "retrieval_scope": out.get("retrieval_scope", ""),
            # Keep these in context only during debugging; remove later if too verbose.
            "source_labels": out.get("source_labels", []),
            "search_vectors_response": out.get("search_vectors_response", {}),
            "retrieve_chunks_response": out.get("retrieve_chunks_response", {}),
            "final_prompt": out.get("final_prompt", ""),
        },
    }
