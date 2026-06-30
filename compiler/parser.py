from tokenizer import *
from tree import build_parse_tree


class Parser:
    def __init__(self, bnf_file: str) -> None:
        self.rules = build_parse_tree()
        self.tokenizer = Tokenizer(Definitions)

    
    def read(self, text: str) -> Iterator[Token[Definitions]]:
        token_stream = self.tokenizer.read(text)
        for token in token_stream:
            # yeah, it's kind of fucky to have different statement separators that both mean the same thing but... too bad!
            pass
            



with open("bnf.txt") as f:
    for token in Tokenizer(BNFRules).read(f.read()):
        print(token)
            


            