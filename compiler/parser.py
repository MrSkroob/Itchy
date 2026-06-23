from tokenizer import *


class Parser:
    def __init__(self, bnf_file: str) -> None:
        with open(bnf_file) as f:
            bnf_tokenizer = Tokenizer(BNFRules)
            for token in bnf_tokenizer.read(f.read()):
                if token.kind is BNFRules.Whitespace:
                    continue
                if token.kind is BNFRules.Comment:
                    continue


        
        self.syntax_rules: list[Token[BNFRules]] = []
# class Parser(Generic[TokenRule]):
#     def __init__(self, tokenizer: Tokenizer[TokenRule], bnf_file: str) -> None:
#         with open(bnf_file) as f:
#             bnf_tokenizer = Tokenizer(BNFRules)
#             for token in bnf_tokenizer.read(f.read()):
#                 pass
        
#         self.syntax_rules: dict[BNFRules, list[BNFRules]] = {}
#         self.tokenizer = tokenizer

    
#     def read(self, text: str) -> Iterator[TokenRule]:
        
#         # list of rules to be consumed. 
#         # the first 
#         incomplete_rules: list[str] = ["<chunk>"]

#         for token in self.tokenizer.read(text):
#             current_rule = incomplete_rules.pop()
            

with open("bnf.txt") as f:
    for token in Tokenizer(BNFRules).read(f.read()):
        print(token)
            


            