A toy programming language called 'Itchy' (because a *Scratch* is caused by an *Itch*).

Created using the knowledge of how programming languages work, Python and compilers since I first created this repo for A level computer science (you can find the archive in [itchy-rewrite](https://github.com/MrSkroob/itchy-rewrite)). 

Repo features:
- BNF grammar
- BNF parser
- Language parser
- Language specific AST builder
- Assembler where its output becomes valid Scratch 3.0 json.

Information for things such as the opcode, json format and others was used from:
https://en.scratch-wiki.info/wiki/Scratch_File_Format
https://en.scratch-wiki.info/wiki/Blocks
https://github.com/scratchfoundation/scratch-vm
https://github.com/scratchfoundation/scratch-editor 

There's *some* abstractions like for loops, while loops, for *in* loops and return statements.
Return statements use a stack, so they'll work for recursive calls and nested calls like `variable = foo(foo(variable))`

# Examples
```
var alphabet

event event_whenflagclicked() {
    alphabet = "abcdefghijklmnopqrstuvwxyz"

    for i in alphabet {
        looks_say(i)
        control_wait(0.5)
    }
}
```
<img width="450" height="357" alt="image" src="https://github.com/user-attachments/assets/d91fc50f-3fc6-4ac0-8a30-09824cab8a18" />

```
var total
list results

define classify(value: var) {
    if value < 0 {
        data_addtolist("negative", results)
    }
    elseif value == 0 {
        data_addtolist("zero", results)
    }
    elseif value < 10 {
        data_addtolist("small", results)
    }
    else {
        data_addtolist("large", results)
    }
}

event event_whenflagclicked() {
    total = 0
    data_deletealloflist(results)

    for i = 1, 20, 1 {
        classify(i - 8)
    }

    looks_sayforsecs(operator_join("Final total: ", total), 2)
    looks_sayforsecs(operator_join("First result: ", results[1]), 2)
}
```
<img width="765" height="505" alt="image" src="https://github.com/user-attachments/assets/ef6963df-8d11-45d7-917e-6c16e356aa55" />

```
var output

define add_one(value: var) {
    return value + 1
}


event event_whenflagclicked() {
    output = 1
    output = add_one(add_one(output))
    if output == 3 {
        looks_say("true")
    }
    else {
        looks_say("false")
    }    
}
```
<img width="414" height="825" alt="image" src="https://github.com/user-attachments/assets/842cecc2-61b7-418a-9b62-8b2eb12f9bf2" />

# Trying it out:
I've avoided using anything that isn't part of the standard library.

Assuming you have python 3.10+, you can run the main function like so:
```
python main.py <INPUT_FILE>.itch <OUTPUT_FILE>.sb3
```
The file's name (e.g. `Sprite1.itch`) will replace the contents of `Sprite1` in the .sb3 file. 

# Grammar:
```
<program> ::= {<vardefstat>} <chunk> <EOF> 
<chunk> ::= {<stat> {<StatementSeperator>}} [<laststat> {<StatementSeperator>}]
<stat> ::= <wrap> | <whilestat> | <ifstat> | <forstat> | <functionstat> | <eventstat> | <varassignstat> | <functioncall>

<whilestat> ::= <While> <equation> <wrap>
<ifstat> ::= <If> <equation> <wrap> {<ElseIf> <equation> <wrap>} [<Else> <wrap>]
<forstat> ::= <For> <Symbol> <forbody> <wrap>
<functionstat> ::= <Define> [<Warp>] <function>
<eventstat> ::= <Event> <Symbol> <args> <wrap>
<vardefstat> ::= [<Shared>] <Type> <Symbol>
<varassignstat> ::= <var> <Assign> <equation>
<functioncall> ::= <Symbol> <args>

<forbody> ::= "=" <equation> <FieldSeperator> <equation> <FieldSeperator> <equation> | <In> <var>
<namelist> ::= <Symbol> {<FieldSeperator> <Symbol>}
<tableconstructor> ::= "[" [<varlist1>] "]"
<wrap> ::= "{" [<chunk>] "}"
<function> ::= <Symbol> <funcbody>
<funcbody> ::= "(" [<paramlist>] ")" <wrap>
<slice> ::= "[" <equation> "]"
<equation> ::= <comparison>
<comparison> ::= <addition> { ("==" | ">" | "<" | <In> ) <addition> }
<addition> ::= <multiplication> { ("+" | "-") <multiplication> }
<multiplication> ::= <unary> { ("*" | "/" | "and" | "or") <unary> }
<unary> ::= ["-" | "not"] <primary>
<primary> ::= <literals> | "(" <equation> ")"
<var> ::= <Symbol> [<slice>]
<literals> ::= <functioncall> | <tableconstructor> | <var> | <Bool> | <Number> | <String> 
<laststat> ::= <Break> | <Return> [<equation>]
<varlist1> ::= <equation> {<FieldSeperator> <equation>}
<args> ::= "(" [<varlist1>] ")"
<argtype> ::= <Symbol> <Colon> <Type>
<paramlist> ::= <argtype> {<FieldSeperator> <argtype>}

```

