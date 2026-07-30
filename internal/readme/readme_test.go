package readme

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

type catalog struct {
	Tools []struct {
		Slug          string `json:"slug"`
		Documentation string `json:"documentation"`
		SourceURL     string `json:"sourceUrl"`
	} `json:"tools"`
}

var markdownLink = regexp.MustCompile(`\[[^\]]+\]\(([^)]+)\)`)

func TestMarkdownRelativeLinksResolve(t *testing.T) {
	root := repositoryRoot(t)
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			if entry.Name() == ".git" {
				return filepath.SkipDir
			}
			return nil
		}
		if filepath.Ext(path) != ".md" {
			return nil
		}

		source, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		for _, match := range markdownLink.FindAllStringSubmatch(string(source), -1) {
			target := strings.TrimSpace(match[1])
			if strings.Contains(target, "://") ||
				strings.HasPrefix(target, "#") ||
				strings.HasPrefix(target, "mailto:") {
				continue
			}

			target = strings.SplitN(target, "#", 2)[0]
			target = strings.SplitN(target, "?", 2)[0]
			resolved := filepath.Clean(filepath.Join(filepath.Dir(path), target))
			if _, err := os.Stat(resolved); err != nil {
				t.Errorf(
					"%s: relative link %q does not resolve: %v",
					filepath.ToSlash(mustRelative(t, root, path)),
					match[1],
					err,
				)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatalf("walk Markdown files: %v", err)
	}
}

func TestCatalogLinksToRepositoryDocumentation(t *testing.T) {
	root := repositoryRoot(t)
	contents, err := os.ReadFile(filepath.Join(root, "catalog.json"))
	if err != nil {
		t.Fatalf("read catalog: %v", err)
	}

	var manifest catalog
	if err := json.Unmarshal(contents, &manifest); err != nil {
		t.Fatalf("parse catalog: %v", err)
	}
	if len(manifest.Tools) < 1 {
		t.Fatal("catalog has no public projects")
	}

	for _, tool := range manifest.Tools {
		if tool.Slug == "" {
			t.Error("catalog tool has empty slug")
		}
		documentationDirectory := strings.TrimSuffix(
			tool.Documentation,
			"/README.md",
		)
		expectedSource := "https://github.com/Certiv-ai/certiv-labs/tree/main/" + documentationDirectory
		if tool.SourceURL != expectedSource {
			t.Errorf(
				"%s source URL = %q, want %q",
				tool.Slug,
				tool.SourceURL,
				expectedSource,
			)
		}

		readmePath := filepath.Join(root, filepath.FromSlash(tool.Documentation))
		if _, err := os.ReadFile(readmePath); err != nil {
			t.Errorf("%s documentation cannot be read: %v", tool.Slug, err)
		}
	}
}

func TestPublicReadmesContainNoInternalReferences(t *testing.T) {
	root := repositoryRoot(t)
	readmes := []string{
		"README.md",
		"cmd/selectstar/README.md",
		"cmd/integrationtestnames/README.md",
		"tools/claude-pool/README.md",
	}
	for _, relativePath := range readmes {
		contents, err := os.ReadFile(filepath.Join(root, relativePath))
		if err != nil {
			t.Fatalf("read %s: %v", relativePath, err)
		}
		text := string(contents)
		for _, forbidden := range []string{
			"CER-",
			"PR #",
			".claude/",
			"Certiv-ai/api-server",
			"localhost",
			"http://",
		} {
			if strings.Contains(text, forbidden) {
				t.Errorf("%s contains internal or unsafe reference %q", relativePath, forbidden)
			}
		}
	}
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatalf("resolve repository root: %v", err)
	}
	return root
}

func mustRelative(t *testing.T, base, target string) string {
	t.Helper()
	relative, err := filepath.Rel(base, target)
	if err != nil {
		t.Fatalf("make path relative: %v", err)
	}
	return relative
}
