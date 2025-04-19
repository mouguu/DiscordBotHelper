import logging
from typing import Dict, List

class SearchQueryParser:
    """Advanced search syntax parser, supports AND, OR, NOT operators, etc."""
    
    def __init__(self):
        self._logger = logging.getLogger('discord_bot.search.query_parser')
    
    def parse_query(self, query_string: str) -> Dict:
        """Parse search query string into structured conditions"""
        if not query_string or not query_string.strip():
            return {"type": "empty"}
            
        # Preprocessing: Normalize spaces, operators, etc.
        query = query_string.strip()
        
        # Check if it contains advanced operators
        has_advanced_syntax = any(op in query for op in ['OR', '|', 'AND', '&', 'NOT', '-', '"'])
        
        if not has_advanced_syntax:
            # Simple query - all words have an AND relationship
            keywords = [k.strip().lower() for k in query.split() if k.strip()]
            return {
                "type": "simple",
                "keywords": keywords
            }
            
        # Process advanced syntax
        return self._parse_advanced_query(query)
    
    def _parse_advanced_query(self, query: str) -> Dict:
        """Parse advanced search syntax"""
        # Decompose query into tokens
        tokens = self._tokenize(query)
        
        # Build syntax tree
        syntax_tree = self._build_syntax_tree(tokens)
        
        return {
            "type": "advanced",
            "tree": syntax_tree
        }
    
    def _tokenize(self, query: str) -> List[Dict]:
        """Decompose query string into tokens"""
        tokens = []
        i = 0
        query_len = len(query)
        
        while i < query_len:
            char = query[i]
            
            # Skip spaces
            if char.isspace():
                i += 1
                continue
                
            # Handle exact matches within quotes
            if char == '"':
                start = i + 1
                i += 1
                while i < query_len and query[i] != '"':
                    i += 1
                
                if i < query_len:  # Found the closing quote
                    phrase = query[start:i].strip().lower()
                    tokens.append({"type": "phrase", "value": phrase})
                else:  # No closing quote, treat as plain text
                    phrase = query[start-1:].strip().lower()
                    tokens.append({"type": "term", "value": phrase})
                i += 1
                continue
                
            # Handle operators
            if char == '|':
                tokens.append({"type": "operator", "value": "OR"})
                i += 1
                continue
                
            if char == '&':
                tokens.append({"type": "operator", "value": "AND"})
                i += 1
                continue
                
            if char == '-':
                tokens.append({"type": "operator", "value": "NOT"})
                i += 1
                continue
                
            # Handle parentheses
            if char == '(':
                tokens.append({"type": "open_paren"})
                i += 1
                continue
                
            if char == ')':
                tokens.append({"type": "close_paren"})
                i += 1
                continue
                
            # Handle text operators
            if i + 2 < query_len:
                three_chars = query[i:i+3].upper()
                if three_chars == "OR ":
                    tokens.append({"type": "operator", "value": "OR"})
                    i += 3
                    continue
                if three_chars == "AND":
                    if i + 3 >= query_len or query[i+3].isspace():
                        tokens.append({"type": "operator", "value": "AND"})
                        i += 3
                        continue
                if three_chars == "NOT":
                    if i + 3 >= query_len or query[i+3].isspace():
                        tokens.append({"type": "operator", "value": "NOT"})
                        i += 3
                        continue
            
            # Handle normal words
            start = i
            while i < query_len and not (query[i].isspace() or query[i] in '|&-()'):
                i += 1
            
            if i > start:
                term = query[start:i].strip().lower()
                tokens.append({"type": "term", "value": term})
                continue
                
            # If no rules matched, advance one character
            i += 1
            
        return tokens
    
    def _build_syntax_tree(self, tokens: List[Dict]) -> Dict:
        """Build syntax tree from token list"""
        if not tokens:
            return {"type": "empty"}
            
        # If there is only one token, return directly
        if len(tokens) == 1:
            token = tokens[0]
            if token["type"] in ["term", "phrase"]:
                return {"type": "term", "value": token["value"]}
            return {"type": "error", "message": "Invalid single token"}
            
        # Handle simple case: all tokens are terms, connect with AND
        all_terms = all(t["type"] in ["term", "phrase"] for t in tokens)
        if all_terms:
            return {
                "type": "and",
                "children": [{"type": "term", "value": t["value"]} for t in tokens]
            }
            
        # Handle OR operator
        or_indices = [i for i, t in enumerate(tokens) if t["type"] == "operator" and t["value"] == "OR"]
        if or_indices:
            # Split at the OR operator
            chunks = []
            last_idx = 0
            for idx in or_indices:
                if idx > last_idx:
                    chunks.append(tokens[last_idx:idx])
                last_idx = idx + 1
            if last_idx < len(tokens):
                chunks.append(tokens[last_idx:])
                
            # Recursively process each block
            children = []
            for chunk in chunks:
                if chunk:
                    children.append(self._build_syntax_tree(chunk))
                    
            return {
                "type": "or",
                "children": children
            }
            
        # Handle NOT operator (simplified processing)
        not_indices = [i for i, t in enumerate(tokens) if t["type"] == "operator" and t["value"] == "NOT"]
        if not_indices:
            # Simplification: only handle prefix NOT
            if not_indices[0] == 0 and len(tokens) > 1:
                return {
                    "type": "not",
                    "child": self._build_syntax_tree(tokens[1:])
                }
                
        # Default to connecting all non-operator tokens with AND
        terms = [t for t in tokens if t["type"] in ["term", "phrase"]]
        if terms:
            return {
                "type": "and",
                "children": [{"type": "term", "value": t["value"]} for t in terms]
            }
            
        return {"type": "error", "message": "Unable to parse query"}
    
    def evaluate(self, syntax_tree: Dict, content: str) -> bool:
        """Evaluate if content matches search conditions"""
        if not content:
            return False
            
        content_lower = content.lower()
        
        # Handle empty tree
        if syntax_tree["type"] == "empty":
            return True
            
        # Handle term matching
        if syntax_tree["type"] == "term":
            return syntax_tree["value"] in content_lower
            
        # Handle AND operator
        if syntax_tree["type"] == "and":
            return all(self.evaluate(child, content) for child in syntax_tree["children"])
            
        # Handle OR operator
        if syntax_tree["type"] == "or":
            return any(self.evaluate(child, content) for child in syntax_tree["children"])
            
        # Handle NOT operator
        if syntax_tree["type"] == "not":
            return not self.evaluate(syntax_tree["child"], content)
            
        # Handle errors
        if syntax_tree["type"] == "error":
            self._logger.warning(f"Search syntax error: {syntax_tree.get('message', 'Unknown error')}")
            return False
            
        # Unknown type
        self._logger.warning(f"Unknown search condition type: {syntax_tree['type']}")
        return False 