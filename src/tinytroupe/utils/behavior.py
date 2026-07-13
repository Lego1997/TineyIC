"""
Various utility functions for behavior analysis and action similarity computation.
"""

import re
from collections import Counter


_WORD_TOKEN_PATTERN = re.compile(r"\b[\w']+\b", re.UNICODE)


def _normalized_word_tokens(content: object) -> list[str]:
    """Return case-normalized word tokens for repetition comparison."""
    if not isinstance(content, str):
        content = str(content or "")
    return _WORD_TOKEN_PATTERN.findall(content.casefold())


def _token_shingle_counts(content: object) -> Counter:
    """Count normalized token bigrams, falling back to unigrams."""
    tokens = _normalized_word_tokens(content)
    if len(tokens) < 2:
        return Counter(tokens)
    return Counter(zip(tokens, tokens[1:]))


def _compute_single_action_jaccard_similarity(current_action, proposed_action):
    """
    Helper function to compute Jaccard similarity between two single actions.
    
    Args:
        current_action (dict): The current action to compare against.
        proposed_action (dict): The proposed action to compare.
        
    Returns:
        float: The Jaccard similarity score.
    """
    # Check if the action type and target are the same
    if ("type" in current_action) and ("type" in proposed_action) and ("target" in current_action) and ("target" in proposed_action) and \
            (current_action["type"] != proposed_action["type"] or current_action["target"] != proposed_action["target"]):
        return 0.0
    
    # Compute multiset Jaccard over normalized token bigrams. Applying Jaccard
    # directly to strings compares character multisets, while token sets lose
    # both frequency and local claim order. Counted bigrams retain near-copy
    # detection without conflating reordered, potentially opposite claims.
    current_action_content = current_action.get("content", "")
    proposed_action_content = proposed_action.get("content", "")
    current_shingles = _token_shingle_counts(current_action_content)
    proposed_shingles = _token_shingle_counts(proposed_action_content)
    union_size = sum((current_shingles | proposed_shingles).values())
    if union_size == 0:
        return 1.0
    intersection_size = sum(
        (current_shingles & proposed_shingles).values()
    )
    return intersection_size / union_size


def next_action_jaccard_similarity(agent, proposed_next_action):
    """
    Computes the Jaccard similarity between the agent's current action and a proposed next action,
    modulo target and type (i.e., similarity will be computed using only the content, provided that the action 
    type and target are the same). If the action type or target is different, the similarity will be 0.

    Jaccard similarity is computed over counted normalized token bigrams: the
    multiset intersection size divided by the multiset union size.

    Args:
        agent (TinyPerson): The agent whose current action is to be compared.
        proposed_next_action (dict or list): The proposed next action (or list of actions) to be compared 
            against the agent's current action. If a list is provided, returns the maximum similarity 
            across all actions in the list.

    Returns:
        float: The Jaccard similarity score between the agent's current action and the proposed next action.
            If proposed_next_action is a list, returns the maximum similarity among all actions.
    """
    # Get the agent's current action
    current_action = agent.last_remembered_action()
    
    if current_action is None:
        return 0.0
    
    # Handle the case where proposed_next_action is a list of actions
    if isinstance(proposed_next_action, list):
        if not proposed_next_action:
            return 0.0
        # Return the maximum similarity across all actions in the list
        max_similarity = 0.0
        for action in proposed_next_action:
            if isinstance(action, dict):
                similarity = _compute_single_action_jaccard_similarity(current_action, action)
                max_similarity = max(max_similarity, similarity)
        return max_similarity
    
    # Single action case
    return _compute_single_action_jaccard_similarity(current_action, proposed_next_action)
