import re

def similarity(a: str, b: str) -> float:
    """Calculates Jaccard similarity between two strings."""
    def tokenize(s: str) -> set:
        s = str(s or '').lower()
        s = re.sub(r'[^a-z0-9 ]', ' ', s)
        tokens = [t for t in s.split() if t]
        return set(tokens)
    
    set_a = set(tokenize(a))
    set_b = set(tokenize(b))
    
    if not set_a or not set_b:
        return 0.0
        
    intersection = len(set_a.intersection(set_b))
    return intersection / max(len(set_a), len(set_b))

def levenshtein(a: str, b: str) -> int:
    """Calculates Levenshtein distance between two strings."""
    a = str(a or '')
    b = str(b or '')
    if len(a) < len(b):
        return levenshtein(b, a)

    if len(b) == 0:
        return len(a)

    previous_row = range(len(b) + 1)
    for i, c1 in enumerate(a):
        current_row = [i + 1]
        for j, c2 in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]
