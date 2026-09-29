"""Transparent synthetic respondents for questionnaire prototyping.

All bundled personas and their answers are synthetic. They are not a sample of
real people and should never be presented as empirical survey findings.
"""

from .demo import demo_questionnaire, generate_demo_panel
from .engine import run_survey
from .models import Persona, Question, Questionnaire, SurveyResult
from .sampling import stratified_sample

__version__ = "0.1.0"

__all__ = [
    "Persona", "Question", "Questionnaire", "SurveyResult",
    "demo_questionnaire", "generate_demo_panel", "stratified_sample", "run_survey",
]
