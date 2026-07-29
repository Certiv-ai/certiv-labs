// Command integrationtestnames finds integration-tagged Go tests that a
// prefix-based CI -run filter would silently skip.
package main

import (
	"flag"
	"fmt"
	"go/ast"
	"go/build/constraint"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

func main() {
	prefix := flag.String(
		"prefix",
		"TestIntegration_",
		"required name prefix for integration-tagged test functions",
	)
	flag.Parse()

	roots := flag.Args()
	if len(roots) == 0 {
		roots = []string{"."}
	}

	violations, err := checkRoots(roots, *prefix)
	if err != nil {
		fmt.Fprintln(os.Stderr, "integrationtestnames:", err)
		os.Exit(2)
	}
	if len(violations) == 0 {
		return
	}

	for _, violation := range violations {
		fmt.Fprintln(os.Stderr, violation)
	}
	fmt.Fprintf(
		os.Stderr,
		"\n%d integration test(s) do not start with %q. Rename the top-level test or change --prefix to match the CI -run filter.\n",
		len(violations),
		*prefix,
	)
	os.Exit(1)
}

func checkRoots(roots []string, prefix string) ([]string, error) {
	if prefix == "" || !strings.HasPrefix(prefix, "Test") {
		return nil, fmt.Errorf("prefix must be a non-empty Go test prefix beginning with \"Test\"")
	}

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
			if !strings.HasSuffix(path, "_test.go") || seen[path] {
				return nil
			}

			seen[path] = true
			found, err := checkFile(path, prefix)
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

func checkFile(path, requiredPrefix string) ([]string, error) {
	source, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if !integrationConstrained(source) {
		return nil, nil
	}

	fileSet := token.NewFileSet()
	file, err := parser.ParseFile(fileSet, path, source, 0)
	if err != nil {
		return nil, err
	}

	var violations []string
	for _, declaration := range file.Decls {
		function, ok := declaration.(*ast.FuncDecl)
		if !ok || function.Recv != nil {
			continue
		}
		if !isTestFunction(function) ||
			strings.HasPrefix(function.Name.Name, requiredPrefix) {
			continue
		}

		line := fileSet.Position(function.Pos()).Line
		violations = append(violations, fmt.Sprintf(
			"%s:%d: %s must start with %s (integration-tagged test)",
			path,
			line,
			function.Name.Name,
			requiredPrefix,
		))
	}

	return violations, nil
}

func isTestFunction(function *ast.FuncDecl) bool {
	if !strings.HasPrefix(function.Name.Name, "Test") {
		return false
	}

	parameters := function.Type.Params
	if parameters == nil || len(parameters.List) != 1 {
		return false
	}

	pointer, ok := parameters.List[0].Type.(*ast.StarExpr)
	if !ok {
		return false
	}
	selector, ok := pointer.X.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	identifier, ok := selector.X.(*ast.Ident)
	if !ok {
		return false
	}

	return identifier.Name == "testing" && selector.Sel.Name == "T"
}

func integrationConstrained(source []byte) bool {
	for _, line := range headerCommentLines(source) {
		if !constraint.IsGoBuild(line) {
			continue
		}

		expression, err := constraint.Parse(line)
		if err != nil {
			return false
		}
		withIntegration := expression.Eval(func(tag string) bool {
			return tag == "integration"
		})
		withoutIntegration := expression.Eval(func(string) bool {
			return false
		})
		return withIntegration && !withoutIntegration
	}
	return false
}

func headerCommentLines(source []byte) []string {
	var lines []string
	for _, rawLine := range strings.Split(string(source), "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" {
			continue
		}
		if !strings.HasPrefix(line, "//") {
			break
		}
		lines = append(lines, line)
	}
	return lines
}
