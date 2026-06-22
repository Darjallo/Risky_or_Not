# -*- coding: utf-8 -*-
"""
Created on Mon Apr 20 18:41:38 2026

@author: oppna
"""

from ethelflow.tutor.dataframecourseprovider import DataFrameCourseProvider
from ethelflow.tutor.create_course_content import DF_COURSE
from ethelflow.tutor.state import TutorState 

provider = DataFrameCourseProvider(DF_COURSE, course_id="plant_biology_101")
COURSE = provider.to_course_json_like()
PROVIDERS = {"plant_biology_101": provider}

PASS_SCORE = 0.5
MAX_WRONG_ATTEMPTS = 2


    
def get_provider(state: TutorState):
    return PROVIDERS[state["course_id"]]