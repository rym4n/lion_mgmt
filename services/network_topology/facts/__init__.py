"""Candidate conversion, evidence validation, and Facts merge boundaries."""

from .candidate_conversion import (
    ConversionResult,
    convert_candidate_batch,
    convert_candidates,
)
from .merge import MergeResult, merge_fact_results

__all__ = (
    "ConversionResult",
    "MergeResult",
    "convert_candidate_batch",
    "convert_candidates",
    "merge_fact_results",
)
