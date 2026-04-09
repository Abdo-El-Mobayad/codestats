; =============================================================================
; CodeStats -- JavaScript symbol and import queries
; tree-sitter-javascript >= 0.23
; Adapted from Repowise (https://github.com/repowise-dev/repowise)
; =============================================================================

; ---------------------------------------------------------------------------
; Symbols
; ---------------------------------------------------------------------------

(function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(generator_function_declaration
  name: (identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

(class_declaration
  name: (identifier) @symbol.name
) @symbol.def

(method_definition
  name: (property_identifier) @symbol.name
  parameters: (formal_parameters) @symbol.params
) @symbol.def

; Arrow function assigned to const/let
(lexical_declaration
  (variable_declarator
    name: (identifier) @symbol.name
    value: (arrow_function
      parameters: (formal_parameters) @symbol.params
    )
  )
) @symbol.def

; ---------------------------------------------------------------------------
; Imports
; ---------------------------------------------------------------------------

(import_statement
  source: (string) @import.module
) @import.statement

; require('./foo')
(call_expression
  function: (identifier) @_fn
  arguments: (arguments (string) @require.module)
  (#eq? @_fn "require")
) @require.statement

; import('./foo')
(call_expression
  function: (import) @_imp
  arguments: (arguments (string) @dynamic_import.module)
) @dynamic_import.statement

; export { X } from './foo'
(export_statement
  source: (string) @reexport.module
) @reexport.statement
