package main

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestCheckFile(t *testing.T) {
	source, err := os.ReadFile(filepath.Join("testdata", "sample.go.txt"))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}

	path := filepath.Join(t.TempDir(), "sample.go")
	if err := os.WriteFile(path, source, 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}

	violations, err := checkFile(path)
	if err != nil {
		t.Fatalf("check file: %v", err)
	}
	if len(violations) != 5 {
		t.Fatalf(
			"got %d violations, want 5:\n%s",
			len(violations),
			strings.Join(violations, "\n"),
		)
	}

	lines := strings.Split(string(source), "\n")
	for _, violation := range violations {
		line := violationLine(t, violation)
		if line < 1 || line > len(lines) {
			t.Fatalf("finding points outside fixture: %q", violation)
		}
		if !selectStar.MatchString(lines[line-1]) {
			t.Errorf(
				"finding points at a line without SELECT *:\n%s\nline %d: %q",
				violation,
				line,
				lines[line-1],
			)
		}
	}
}

func TestCheckRootsSkipsIgnoredDirectoriesAndDuplicateRoots(t *testing.T) {
	root := t.TempDir()
	writeGoFile(t, root, "live/query.go", `package live
func f(db interface{ Get(any, string) error }) {
	_ = db.Get(nil, "SELECT * FROM live")
}`)
	writeGoFile(t, root, "vendor/query.go", `package vendor
func f(db interface{ Get(any, string) error }) {
	_ = db.Get(nil, "SELECT * FROM vendor")
}`)
	writeGoFile(t, root, ".gopath/query.go", `not valid go source`)
	writeGoFile(t, root, "_worktree/query.go", `not valid go source`)
	writeGoFile(t, root, "testdata/query.go", `not valid go source`)

	violations, err := checkRoots([]string{root, root + "/..."})
	if err != nil {
		t.Fatalf("check roots: %v", err)
	}
	if len(violations) != 1 {
		t.Fatalf("got %d violations, want 1: %v", len(violations), violations)
	}
	if !strings.Contains(violations[0], filepath.Join("live", "query.go")) {
		t.Fatalf("finding did not come from live directory: %q", violations[0])
	}
}

func writeGoFile(t *testing.T, root, relativePath, contents string) {
	t.Helper()
	path := filepath.Join(root, relativePath)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatalf("create fixture directory: %v", err)
	}
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
}

func violationLine(t *testing.T, violation string) int {
	t.Helper()
	parts := strings.SplitN(violation, ":", 3)
	if len(parts) < 3 {
		t.Fatalf("malformed violation: %q", violation)
	}
	line, err := strconv.Atoi(parts[1])
	if err != nil {
		t.Fatalf("bad line number in %q: %v", violation, err)
	}
	return line
}
