package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCheckFileReportsSkippedIntegrationTest(t *testing.T) {
	path := writeTestFile(t, `//go:build integration

package sample

import "testing"

func TestIntegration_Visible(t *testing.T) {}
func TestInvisible(t *testing.T) {}
func BenchmarkIgnored(b *testing.B) {}
`)

	violations, err := checkFile(path, "TestIntegration_")
	if err != nil {
		t.Fatalf("check file: %v", err)
	}
	if len(violations) != 1 {
		t.Fatalf("got %d violations, want 1: %v", len(violations), violations)
	}
	if !strings.Contains(violations[0], "TestInvisible") {
		t.Fatalf("finding does not identify skipped test: %q", violations[0])
	}
}

func TestCheckFileIgnoresOrdinaryUnitTest(t *testing.T) {
	path := writeTestFile(t, `package sample

import "testing"

func TestOrdinary(t *testing.T) {}
`)

	violations, err := checkFile(path, "TestIntegration_")
	if err != nil {
		t.Fatalf("check file: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("got unexpected violations: %v", violations)
	}
}

func TestCheckFileSupportsCustomPrefix(t *testing.T) {
	path := writeTestFile(t, `//go:build integration && !windows

package sample

import "testing"

func TestE2E_Database(t *testing.T) {}
func TestIntegration_OldConvention(t *testing.T) {}
`)

	violations, err := checkFile(path, "TestE2E_")
	if err != nil {
		t.Fatalf("check file: %v", err)
	}
	if len(violations) != 1 ||
		!strings.Contains(violations[0], "TestIntegration_OldConvention") {
		t.Fatalf("unexpected violations: %v", violations)
	}
}

func TestCheckRootsRejectsInvalidPrefix(t *testing.T) {
	if _, err := checkRoots([]string{"."}, "Integration_"); err == nil {
		t.Fatal("expected invalid prefix error")
	}
}

func TestCheckRootsSkipsHiddenAndDependencyDirectories(t *testing.T) {
	root := t.TempDir()
	for _, directory := range []string{
		".gopath",
		".worktrees",
		"_scratch",
		"vendor",
		"node_modules",
		"mocks",
		"testdata",
	} {
		path := filepath.Join(root, directory, "hidden_test.go")
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatalf("create skipped directory: %v", err)
		}
		if err := os.WriteFile(path, []byte("not valid go source"), 0o600); err != nil {
			t.Fatalf("write skipped fixture: %v", err)
		}
	}

	violations, err := checkRoots([]string{root}, "TestIntegration_")
	if err != nil {
		t.Fatalf("check roots: %v", err)
	}
	if len(violations) != 0 {
		t.Fatalf("got unexpected violations: %v", violations)
	}
}

func writeTestFile(t *testing.T, source string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "sample_test.go")
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	return path
}
