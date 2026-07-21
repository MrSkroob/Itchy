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

Notes:
Error reporting is hit or miss (extremely vague - only tells you if something has gone wrong and nothing else), and you may need to edit file locations since I haven't used the `path` module to do relative pathing.

# Examples
```
string alphabet

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
number total
list results

define classify(value: number) {
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

# 

