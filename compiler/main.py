from parser import Parser


parser = Parser()

with open("code.txt") as f:
    result = parser.read(f.read())
    print(result)
