// Command selectstar reports SELECT * strings passed to common Go sqlx query
// methods.
//
// This is a rollout-safety check, not a style preference. When a migration adds
// a column before every old application instance has stopped serving, sqlx
// strict scans can fail because the old destination struct does not contain the
// new column. Explicit projections keep query results stable across that window.
//
// The check deliberately stays narrow: it reports SELECT * only when the string
// is an argument to a recognized query method. It resolves local variables,
// string concatenation, and fmt.Sprintf format strings. Use
// `//selectstar:allow <reason>` for a reviewed exception.
package main

import (
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

var selectStar = regexp.MustCompile(`(?i)\bselect\s+\*`)

var sqlxQueryMethods = map[string]bool{
	"Get": true, "GetContext": true,
	"Select": true, "SelectContext": true,
	"Query": true, "QueryContext": true,
	"QueryRow": true, "QueryRowContext": true,
	"Queryx": true, "QueryxContext": true,
	"QueryRowx": true, "QueryRowxContext": true,
	"Exec": true, "ExecContext": true,
	"MustExec": true, "MustExecContext": true,
	"NamedExec": true, "NamedExecContext": true,
	"NamedQuery": true, "NamedQueryContext": true,
	"Prepare": true, "PrepareContext": true,
	"Preparex": true, "PreparexContext": true,
	"PrepareNamed": true, "PrepareNamedContext": true,
	"Rebind": true, "In": true, "Named": true,
}

const allowDirective = "selectstar:allow"

func main() {
	fail := flag.Bool("fail", false, "exit non-zero when findings are present")
	flag.Parse()

	roots := flag.Args()
	if len(roots) == 0 {
		roots = []string{"."}
	}

	violations, err := checkRoots(roots)
	if err != nil {
		fmt.Fprintln(os.Stderr, "selectstar:", err)
		os.Exit(2)
	}

	for _, violation := range violations {
		fmt.Fprintln(os.Stderr, violation)
	}
	if len(violations) == 0 {
		return
	}

	fmt.Fprintf(
		os.Stderr,
		"\n%d sqlx call(s) use `SELECT *`. List columns explicitly so query results remain stable during schema changes. If a site is safe, annotate it with `//%s <reason>`.\n",
		len(violations),
		allowDirective,
	)
	if *fail {
		os.Exit(1)
	}
}

func checkRoots(roots []string) ([]string, error) {
	var violations []string
	seen := make(map[string]bool)

	for _, root := range roots {
		dir := strings.TrimSuffix(strings.TrimSuffix(root, "..."), "/")
		if dir == "" {
			dir = "."
		}

		err := filepath.WalkDir(dir, func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if entry.IsDir() {
				if skipDirectory(entry.Name()) {
					return filepath.SkipDir
				}
				return nil
			}
			if !strings.HasSuffix(path, ".go") || seen[path] {
				return nil
			}

			seen[path] = true
			found, err := checkFile(path)
			if err != nil {
				return fmt.Errorf("%s: %w", path, err)
			}
			violations = append(violations, found...)
			return nil
		})
		if err != nil {
			return nil, err
		}
	}

	return violations, nil
}

func skipDirectory(name string) bool {
	if strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") {
		return true
	}
	switch name {
	case "vendor", "node_modules", "mocks", "testdata":
		return true
	default:
		return false
	}
}

func checkFile(path string) ([]string, error) {
	src, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, path, src, parser.ParseComments)
	if err != nil {
		return nil, err
	}

	allowedLines := allowedLineSet(fset, file)
	packageVariables := collectAssignments(file.Decls)
	reported := map[int]bool{}
	var violations []string

	report := func(callPosition token.Pos, fragments []stringFragment) {
		for _, fragment := range fragments {
			if !selectStar.MatchString(fragment.text) {
				continue
			}

			line := fset.Position(fragment.position).Line
			callLine := fset.Position(callPosition).Line
			if allowedLines[line] || allowedLines[line-1] ||
				allowedLines[callLine] || allowedLines[callLine-1] {
				return
			}
			if reported[line] {
				return
			}

			reported[line] = true
			violations = append(violations, fmt.Sprintf(
				"%s:%d: sqlx query uses `SELECT *` — list columns explicitly for rollout safety",
				path,
				line,
			))
			return
		}
	}

	for _, declaration := range file.Decls {
		function, ok := declaration.(*ast.FuncDecl)
		if !ok || function.Body == nil {
			continue
		}

		scope := mergeVariables(
			packageVariables,
			collectAssignments([]ast.Decl{function}),
		)
		ast.Inspect(function.Body, func(node ast.Node) bool {
			call, ok := node.(*ast.CallExpr)
			if !ok {
				return true
			}

			selector, ok := call.Fun.(*ast.SelectorExpr)
			if !ok || !sqlxQueryMethods[selector.Sel.Name] {
				return true
			}

			var fragments []stringFragment
			for _, argument := range call.Args {
				fragments = append(
					fragments,
					resolveFragments(argument, scope, map[string]bool{}, 0)...,
				)
			}
			report(call.Lparen, fragments)
			return true
		})
	}

	return violations, nil
}

type stringFragment struct {
	text     string
	position token.Pos
}

func resolveFragments(
	expression ast.Expr,
	scope map[string][]ast.Expr,
	visited map[string]bool,
	depth int,
) []stringFragment {
	if depth > 16 {
		return nil
	}

	switch value := expression.(type) {
	case *ast.BasicLit:
		if value.Kind != token.STRING {
			return nil
		}
		text, err := strconv.Unquote(value.Value)
		if err != nil {
			return nil
		}
		return []stringFragment{{text: text, position: value.Pos()}}

	case *ast.Ident:
		if visited[value.Name] {
			return nil
		}
		visited[value.Name] = true
		var fragments []stringFragment
		for _, rightHandSide := range scope[value.Name] {
			fragments = append(
				fragments,
				resolveFragments(rightHandSide, scope, visited, depth+1)...,
			)
		}
		return fragments

	case *ast.BinaryExpr:
		if value.Op != token.ADD {
			return nil
		}
		return append(
			resolveFragments(value.X, scope, visited, depth+1),
			resolveFragments(value.Y, scope, visited, depth+1)...,
		)

	case *ast.ParenExpr:
		return resolveFragments(value.X, scope, visited, depth+1)

	case *ast.CallExpr:
		selector, ok := value.Fun.(*ast.SelectorExpr)
		if !ok || len(value.Args) == 0 {
			return nil
		}
		identifier, ok := selector.X.(*ast.Ident)
		if !ok || identifier.Name != "fmt" ||
			!strings.HasPrefix(selector.Sel.Name, "Sprint") {
			return nil
		}
		return resolveFragments(value.Args[0], scope, visited, depth+1)

	default:
		return nil
	}
}

func collectAssignments(declarations []ast.Decl) map[string][]ast.Expr {
	variables := map[string][]ast.Expr{}
	add := func(name string, expression ast.Expr) {
		if name != "_" && expression != nil {
			variables[name] = append(variables[name], expression)
		}
	}

	for _, declaration := range declarations {
		ast.Inspect(declaration, func(node ast.Node) bool {
			switch statement := node.(type) {
			case *ast.AssignStmt:
				if statement.Tok != token.DEFINE &&
					statement.Tok != token.ASSIGN &&
					statement.Tok != token.ADD_ASSIGN {
					return true
				}
				for index, leftHandSide := range statement.Lhs {
					identifier, ok := leftHandSide.(*ast.Ident)
					if !ok || len(statement.Rhs) != len(statement.Lhs) {
						continue
					}
					add(identifier.Name, statement.Rhs[index])
				}

			case *ast.ValueSpec:
				for index, identifier := range statement.Names {
					if index < len(statement.Values) {
						add(identifier.Name, statement.Values[index])
					}
				}
			}
			return true
		})
	}

	return variables
}

func mergeVariables(
	base map[string][]ast.Expr,
	overlay map[string][]ast.Expr,
) map[string][]ast.Expr {
	merged := make(map[string][]ast.Expr, len(base)+len(overlay))
	for name, expressions := range base {
		merged[name] = expressions
	}
	for name, expressions := range overlay {
		merged[name] = append(merged[name], expressions...)
	}
	return merged
}

func allowedLineSet(fset *token.FileSet, file *ast.File) map[int]bool {
	allowed := map[int]bool{}
	for _, commentGroup := range file.Comments {
		for _, comment := range commentGroup.List {
			if strings.Contains(comment.Text, allowDirective) {
				allowed[fset.Position(comment.Slash).Line] = true
			}
		}
	}
	return allowed
}
