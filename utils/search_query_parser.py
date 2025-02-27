import logging
from typing import Dict, List

class SearchQueryParser:
    """高级搜索语法解析器，支持 AND, OR, NOT 等操作符"""
    
    def __init__(self):
        self._logger = logging.getLogger('discord_bot.search.query_parser')
    
    def parse_query(self, query_string: str) -> Dict:
        """解析搜索查询字符串为结构化条件"""
        if not query_string or not query_string.strip():
            return {"type": "empty"}
            
        # 预处理：规范化空格、操作符等
        query = query_string.strip()
        
        # 检测是否包含高级操作符
        has_advanced_syntax = any(op in query for op in ['OR', '|', 'AND', '&', 'NOT', '-', '"'])
        
        if not has_advanced_syntax:
            # 简单查询 - 所有词都是 AND 关系
            keywords = [k.strip().lower() for k in query.split() if k.strip()]
            return {
                "type": "simple",
                "keywords": keywords
            }
            
        # 处理高级语法
        return self._parse_advanced_query(query)
    
    def _parse_advanced_query(self, query: str) -> Dict:
        """解析高级搜索语法"""
        # 将查询分解为词元
        tokens = self._tokenize(query)
        
        # 构建语法树
        syntax_tree = self._build_syntax_tree(tokens)
        
        return {
            "type": "advanced",
            "tree": syntax_tree
        }
    
    def _tokenize(self, query: str) -> List[Dict]:
        """将查询字符串分解为词元"""
        tokens = []
        i = 0
        query_len = len(query)
        
        while i < query_len:
            char = query[i]
            
            # 跳过空格
            if char.isspace():
                i += 1
                continue
                
            # 处理引号内的精确匹配
            if char == '"':
                start = i + 1
                i += 1
                while i < query_len and query[i] != '"':
                    i += 1
                
                if i < query_len:  # 找到了结束引号
                    phrase = query[start:i].strip().lower()
                    tokens.append({"type": "phrase", "value": phrase})
                else:  # 没有结束引号，当作普通文本
                    phrase = query[start-1:].strip().lower()
                    tokens.append({"type": "term", "value": phrase})
                i += 1
                continue
                
            # 处理操作符
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
                
            # 处理括号
            if char == '(':
                tokens.append({"type": "open_paren"})
                i += 1
                continue
                
            if char == ')':
                tokens.append({"type": "close_paren"})
                i += 1
                continue
                
            # 处理文本操作符
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
            
            # 处理普通词语
            start = i
            while i < query_len and not (query[i].isspace() or query[i] in '|&-()'):
                i += 1
            
            if i > start:
                term = query[start:i].strip().lower()
                tokens.append({"type": "term", "value": term})
                continue
                
            # 如果没有匹配任何规则，前进一个字符
            i += 1
            
        return tokens
    
    def _build_syntax_tree(self, tokens: List[Dict]) -> Dict:
        """从词元列表构建语法树"""
        if not tokens:
            return {"type": "empty"}
            
        # 如果只有一个词元，直接返回
        if len(tokens) == 1:
            token = tokens[0]
            if token["type"] in ["term", "phrase"]:
                return {"type": "term", "value": token["value"]}
            return {"type": "error", "message": "Invalid single token"}
            
        # 处理简单情况：所有词元都是术语，用 AND 连接
        all_terms = all(t["type"] in ["term", "phrase"] for t in tokens)
        if all_terms:
            return {
                "type": "and",
                "children": [{"type": "term", "value": t["value"]} for t in tokens]
            }
            
        # 处理 OR 操作符
        or_indices = [i for i, t in enumerate(tokens) if t["type"] == "operator" and t["value"] == "OR"]
        if or_indices:
            # 分割在 OR 操作符处
            chunks = []
            last_idx = 0
            for idx in or_indices:
                if idx > last_idx:
                    chunks.append(tokens[last_idx:idx])
                last_idx = idx + 1
            if last_idx < len(tokens):
                chunks.append(tokens[last_idx:])
                
            # 递归处理每个块
            children = []
            for chunk in chunks:
                if chunk:
                    children.append(self._build_syntax_tree(chunk))
                    
            return {
                "type": "or",
                "children": children
            }
            
        # 处理 NOT 操作符（简化处理）
        not_indices = [i for i, t in enumerate(tokens) if t["type"] == "operator" and t["value"] == "NOT"]
        if not_indices:
            # 简化：只处理前缀 NOT
            if not_indices[0] == 0 and len(tokens) > 1:
                return {
                    "type": "not",
                    "child": self._build_syntax_tree(tokens[1:])
                }
                
        # 默认使用 AND 连接所有非操作符词元
        terms = [t for t in tokens if t["type"] in ["term", "phrase"]]
        if terms:
            return {
                "type": "and",
                "children": [{"type": "term", "value": t["value"]} for t in terms]
            }
            
        return {"type": "error", "message": "Unable to parse query"}
    
    def evaluate(self, syntax_tree: Dict, content: str) -> bool:
        """评估内容是否匹配搜索条件"""
        if not content:
            return False
            
        content_lower = content.lower()
        
        # 处理空树
        if syntax_tree["type"] == "empty":
            return True
            
        # 处理术语匹配
        if syntax_tree["type"] == "term":
            return syntax_tree["value"] in content_lower
            
        # 处理 AND 操作符
        if syntax_tree["type"] == "and":
            return all(self.evaluate(child, content) for child in syntax_tree["children"])
            
        # 处理 OR 操作符
        if syntax_tree["type"] == "or":
            return any(self.evaluate(child, content) for child in syntax_tree["children"])
            
        # 处理 NOT 操作符
        if syntax_tree["type"] == "not":
            return not self.evaluate(syntax_tree["child"], content)
            
        # 处理错误
        if syntax_tree["type"] == "error":
            self._logger.warning(f"搜索语法错误: {syntax_tree.get('message', '未知错误')}")
            return False
            
        # 未知类型
        self._logger.warning(f"未知搜索条件类型: {syntax_tree['type']}")
        return False 