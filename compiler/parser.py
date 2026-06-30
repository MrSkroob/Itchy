from tokenizer import *
from tree import build_parse_tree


class Parser:
    def __init__(self, bnf_file: str) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions)

        current_node = self.rules

        with open(bnf_file) as f:
            bnf_tokenizer = Tokenizer(BNFRules)
            
            for token in bnf_tokenizer.read(f.read()):
                # no need to interpret these rules.
                if token.kind is BNFRules.Whitespace:
                    continue
                if token.kind is BNFRules.Comment:
                    continue

                node.append(node)
                # current_node.children.append(node)

    
    def read(self, text: str) -> Iterator[Token[Definitions]]:
        token_stream = self.tokenizer.read(text)
        for token in token_stream:
            # yeah, it's kind of fucky to have different statement separators that both mean the same thing but... too bad!
            if token.kind is Definitions.StatementSeperator or GenericRules.StatementSeperator:
                continue
            



with open("bnf.txt") as f:
    for token in Tokenizer(BNFRules).read(f.read()):
        print(token)
            


            