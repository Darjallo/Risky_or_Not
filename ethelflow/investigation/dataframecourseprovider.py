# -*- coding: utf-8 -*-
"""
Created on Thu Apr 16 16:27:52 2026

@author: oppna
"""
import pandas as pd
from typing import Optional, List, Dict, Any
import json

class DataFrameCourseProvider:
    def __init__(self, df: pd.DataFrame, course_id: str):
        self.df = df[df["course_id"] == course_id].copy()
        self.course_id = course_id

        # Ensure stable ordering
        self.df["order"] = self.df["order"].astype(int)

    def list_topics(self) -> List[Dict[str, Any]]:
        # topic metadata from distinct topic rows
        topics = (
            self.df[["topic_id", "topic_title", "order", "prerequisites"]]
            .drop_duplicates()
            .sort_values("order")
        )
        out = []
        for _, r in topics.iterrows():
            out.append({
                "topic_id": r["topic_id"],
                "title": r["topic_title"],
                "order": int(r["order"]),
                "prerequisites": json.loads(r["prerequisites"]) if pd.notna(r["prerequisites"]) else [],
            })
        return out

    def get_materials(self, topic_id: str) -> Dict[str, Any]:
        sub = self.df[self.df["topic_id"] == topic_id]

        lesson_text = sub.loc[sub["type"] == "lesson_text", "text"]
        lesson_text = lesson_text.iloc[0] if len(lesson_text) else ""

        key_points = sub.loc[sub["type"] == "key_point", "text"].tolist()
        examples = sub.loc[sub["type"] == "example", "text"].tolist()

        return {
            "lesson_text": lesson_text,
            "key_points": key_points,
            "examples": examples,
        }

    def get_questions(self, topic_id: str) -> List[Dict[str, Any]]:
        sub = self.df[(self.df["topic_id"] == topic_id) & (self.df["type"] == "quiz_question")]
        out = []
        for _, r in sub.iterrows():
            out.append({
                "content_id": r["content_id"],
                "question_text": r["text"],
                "answer_key": json.loads(r["answer_key"]) if pd.notna(r["answer_key"]) else None,
                "rubric": json.loads(r["rubric"]) if pd.notna(r["rubric"]) else None,
            })
        return out

    def get_question_by_id(self, content_id: str) -> Optional[Dict[str, Any]]:
        """
        Return a single question dict by content_id, or None if not found.
        """
        sub = self.df[
            (self.df["content_id"] == content_id) &
            (self.df["type"] == "quiz_question")
        ]

        if sub.empty:
            return None

        r = sub.iloc[0]
        return {
            "content_id": r["content_id"],
            "topic_id": r["topic_id"],
            "question_text": r["text"],
            "answer_key": json.loads(r["answer_key"]) if pd.notna(r.get("answer_key")) and r.get("answer_key") else None,
            "rubric": json.loads(r["rubric"]) if pd.notna(r.get("rubric")) and r.get("rubric") else None,
        }

    def to_course_json_like(self) -> Dict[str, Any]:
        # This recreates something very close to your original COURSE format.
        topics_json = []
        for t in self.list_topics():
            mats = self.get_materials(t["topic_id"])
            qs = self.get_questions(t["topic_id"])
            topics_json.append({
                "topic_id": t["topic_id"],
                "title": t["title"],
                "order": t["order"],
                "prerequisites": t["prerequisites"],
                "materials": {
                    "lesson_text": mats["lesson_text"],
                    "key_points": mats["key_points"],
                    "examples": mats["examples"],
                    # Keep compatibility: control_questions as strings
                    "control_questions": [q["question_text"] for q in qs],
                    # New: richer questions payload if you want it
                    "questions": qs,
                }
            })

        return {
            "course_id": self.course_id,
            "title": None,
            "level": None,
            "description": None,
            "topics": topics_json,
        }
