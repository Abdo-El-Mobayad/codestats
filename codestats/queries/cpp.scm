; =============================================================================
; CodeStats -- C++ symbol and import queries
; tree-sitter-cpp >= 0.23
; (Also used for .c files)
; Adapted from Repowise (https://github.com/repowise-dev/repowise)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

; Function definition: ReturnType funcName(params) { body }
(function_definition
  declarator: (function_declarator
    declarator: (identifier) @symbol.name
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Qualified function definition: ReturnType ClassName::method(params) { }
(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      name: (identifier) @symbol.name
    )
    parameters: (parameter_list) @symbol.params
  )
) @symbol.def

; Class
(class_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; Struct
(struct_specifier
  name: (type_identifier) @symbol.name
) @symbol.def

; Enum
(enum_specifier
  (type_identifier) @symbol.name
) @symbol.def

; Namespace
(namespace_definition
  name: (namespace_identifier) @symbol.name
) @symbol.def

; ---------------------------------------------------------------------------
; Imports (#include directives)
; ---------------------------------------------------------------------------

; #include <header>
(preproc_include
  path: (system_lib_string) @import.module
) @import.statement

; #include "local_header"
(preproc_include
  path: (string_literal) @import.module
) @import.statement
