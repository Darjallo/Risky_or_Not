# -*- coding: utf-8 -*-
"""
Created on Wed Apr 29 22:50:17 2026

@author: oppna

State schema for stage-gated contamination case investigation assistant.
"""


from typing import TypedDict, Dict, Any, Optional, List, Literal

DocumentCategory = Literal[
    "task_description",
    "problem_approach",
    "old_resolved_case",
    "scientific_literature",
    "investigation_data",
    "gold_standard_report",
]

Visibility = Literal[
    "student",
    "stage_gated",
    "evaluator_only",
    "instructor_only",
]

InvestigationIntent = Literal[
    "advance_stage",
    "task_question",
    "evidence_question",
    "hypothesis_submission",
    "hidden_info_request",
    "general_chat",
]


ResponseType = Literal[
    "stage_update",
    "task_answer",
    "evidence_answer",
    "hypothesis_feedback",
    "access_refusal",
    "general_answer",
    "error",
]

class CaseDocument(TypedDict, total=False):
    document_id: str
    title: str
    category: DocumentCategory
    visibility: Visibility

    # For stage-gated investigation documents.
    # Example: release_stage=1 means available from stage 1 onward.
    release_stage: int

    # Optional metadata for debugging and citations
    description: str
    source_type: str
    

class HypothesisReview(TypedDict, total=False):
    student_hypothesis: str

    # Evaluation based on currently available evidence
    supported_by_available_evidence: bool
    missing_evidence: List[str]
    unsupported_claims: List[str]
    plausible_points: List[str]

    # Hidden comparison against gold standard, not directly revealed
    closeness_to_gold_standard: Optional[float]

    # Socratic guidance
    follow_up_question: str
    suggested_next_check: str
    
    
class InvestigationState(TypedDict, total=False):
    # required
    tenant: str 

    # case
    case_id: str
    case_title: str
    
    # current stage
    current_stage: int
    max_stage: int
    
    # conversation history
    last_user_msg: str
    history: List[Dict[str, str]]
    history_text: str
    
    # router
    router_prompt: str
    router_raw_output: str
    intent: InvestigationIntent
    response_type: ResponseType
    
    # Document access
    case_documents: List[CaseDocument]
    allowed_document_ids: List[str]
    blocked_document_ids: List[str]
    # Optional grouping by category
    task_description_document_ids: List[str]
    problem_approach_document_ids: List[str]
    old_resolved_case_document_ids: List[str]
    scientific_literature_document_ids: List[str]
    investigation_data_document_ids: List[str]
    gold_standard_document_ids: List[str]
    
    investigation_stage_documents: Dict[str, List[str]]
    newly_released_document_ids: List[str]
    
    retrieval_scope: Literal["student_visible", "evaluation"]
    
    # RAG
    extractor: str
    method: str
    embedding_space: Optional[str]
    top_k: int
    
    # vector search
    prompts: List[str]
    embeddings: List[List[float]]
    query_embedding: List[float]
    
    search_vectors_response: Dict[str, Any]
    hit_chunk_ids: List[str]
    
    retrieve_chunks_response: Dict[str, Any]
    chunk_texts: List[str]
    chunk_metadata: List[Dict[str, Any]]
    source_labels: List[str]
    
    # prompt construction
    template: Optional[str]
    template_path: Optional[str]
    template_fields: Dict[str, Any]
    final_prompt: str
    
    reasoning_prompt: str
    reasoning_raw_output: str
    
    gold_prompts: List[str]
    gold_embeddings: List[str]
    gold_query_embedding: List[str]
    gold_hit_chunk_ids: List[str]
    gold_chunk_texts: List[str]
    gold_chunk_metadata: List[Dict[str, Any]]
    gold_search_vectors_response: Dict[str, Any]
    gold_retrieve_chunks_response: Dict[str, Any]
    
    # hypothesis review
    submitted_hypothesis: Optional[str]
    hypothesis_review: HypothesisReview
    gold_standard_summary_internal: Optional[str]
    evaluator_prompt: str
    evaluator_raw_output: str
    
    # stage update 
    requested_stage: Optional[int]
    stage_update_message: str
    
    # Access/refusal handling
    requested_hidden_info: Optional[str]
    access_refusal_reason: Optional[str]

    draft_response: str
    debug: Dict[str, Any]
    quit: bool
