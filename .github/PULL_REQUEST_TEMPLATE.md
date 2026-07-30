## What changed

<!-- Describe the user-visible problem and the focused change. -->

## Validation

- [ ] `gofmt -w ./cmd`
- [ ] `go test ./...`
- [ ] `go vet ./...`
- [ ] `(cd tools/claude-pool && python3 -m unittest discover -s tests -v)`
- [ ] `(cd tools/claude-pool && python3 scripts/build_release.py)`
- [ ] Relevant README commands were run
- [ ] README links were checked

## Public-release review

- [ ] No private issue IDs, customer details, internal hosts, or secrets
- [ ] Data handling and network behavior are unchanged or documented
- [ ] New dependencies are justified
- [ ] User-visible behavior and limitations are documented
