# AGENTS.md — WorthIt

## Mission

WorthIt autonomously discovers, investigates, tests, scores, reviews, ranks, and publishes reviews of interesting AI and developer tools.

The central question is:

> Is this actually worth someone's time?

WorthIt must earn its conclusions through evidence.

A repository trending on GitHub is a candidate, not an endorsement.

---

# Operating Priorities

In order:

1. Safety
2. Evidence
3. Correctness
4. Reproducibility
5. Editorial integrity
6. Usefulness
7. Autonomy
8. Product quality
9. Scale

Do not sacrifice the first five to move faster.

---

# Autonomy

You are authorized to:

- inspect the entire repository
- modify application code
- add dependencies when justified
- create tests
- create migrations
- create GitHub Actions workflows
- create documentation
- create the public WorthIt website/blog
- create static publishing infrastructure
- run local tests
- run safe integration tests
- use GitHub APIs
- research public repositories
- delegate bounded tasks to Claude
- commit logical units of work
- deploy WorthIt's public review site when deployment credentials/configuration allow it

Do not stop at planning or scaffolding when implementation can continue.

Prefer completing a working vertical slice.

---

# Claude

Claude is available as a secondary engineering agent.

Inspect the locally installed Claude tooling before assuming CLI syntax.

Use Claude for bounded parallel or adversarial work such as:

- independent architecture review
- security review
- threat modeling
- test-plan criticism
- code review
- UI work
- editorial review
- detecting unsupported conclusions
- checking whether reviews sound machine-generated
- finding edge cases
- evaluating scoring fairness

Codex remains lead engineer.

Do not blindly accept Claude's output.

Review and integrate it.

A useful pattern is:

1. Codex proposes.
2. Claude critiques.
3. Codex reconciles.
4. One implements.
5. The other reviews.

---

# Repository Selection Policy

WorthIt should discover broadly but test selectively.

The default daily goal is:

> Discover broadly, identify the most interesting credible candidates, and deeply evaluate up to 5 repositories per day.

Do not test repositories solely because they appear on GitHub Trending.

Before execution, every repository must pass:

- relevance screening
- interest screening
- provenance screening
- documentation screening
- license screening
- static security screening
- testability screening
- resource feasibility screening

---

# Do Not Use Geography As A Trust Signal

Do not accept or reject software because of:

- developer nationality
- country of origin
- ethnicity
- geographic location

Instead evaluate objective evidence.

A repository should be rejected, delayed, or flagged when it has indicators such as:

- unclear provenance
- suspicious ownership history
- unexplained binary artifacts
- heavily obfuscated code
- encoded executable payloads
- unexplained network communication
- credential harvesting behavior
- cryptocurrency mining
- privilege escalation
- disabling security controls
- unexplained telemetry
- destructive installation behavior
- suspicious dependency substitution
- copied code with unclear licensing
- misleading repository metadata
- fake or obviously manipulated popularity
- README claims that do not match the code
- inaccessible or insufficient documentation
- no practical way for WorthIt to understand how the tool is supposed to operate
- no meaningful testable functionality

Documentation does not have to originate in English.

WorthIt must, however, be able to confidently understand the project's documentation and intended behavior before evaluating it.

If meaning cannot be established reliably:

`SKIP_INSUFFICIENT_DOCUMENTATION`

Do not guess.

---

# Interestingness Gate

WorthIt is not required to review boring projects.

A candidate should normally demonstrate at least one of:

- meaningful recent growth
- genuinely novel capability
- unusually strong developer interest
- useful improvement on an existing workflow
- interesting local/self-hosted capability
- interesting AI infrastructure
- meaningful agent capability
- interesting model/runtime capability
- useful developer tool
- strong technical idea
- unusually bold claims worth testing
- clear real-world utility
- substantial new release of an already interesting project

Down-rank:

- thin wrappers around standard APIs
- obvious clones
- tutorial repositories
- abandoned projects
- generated boilerplate
- repositories with no substantive implementation
- SEO repositories
- link collections
- prompt collections presented as products
- fake benchmarks
- trivial ChatGPT wrappers
- projects whose only novelty is branding

The system should explain why something was considered interesting.

---

# Candidate Trust Classification

Assign one before execution:

- TRUSTED_ENOUGH_TO_TEST
- TEST_WITH_RESTRICTIONS
- REQUIRES_REVIEW
- REJECTED
- INSUFFICIENT_INFORMATION

This classification governs execution.

It is not a statement that the repository is secure.

---

# Never Deploy Candidate Software

WorthIt evaluates third-party software.

WorthIt does **not** deploy arbitrary candidate repositories onto public infrastructure.

Candidate software may only execute inside WorthIt's disposable evaluation environment.

The only system WorthIt should publicly deploy automatically is **WorthIt itself and its generated review site**.

Never expose candidate services directly to the public Internet.

---

# Hostile Code Assumption

Treat every candidate repository as potentially hostile.

Never execute candidate code directly on the host.

Never expose:

- SSH keys
- GitHub credentials
- cloud credentials
- LLM credentials
- home directories
- browser profiles
- Docker socket
- database credentials
- package-manager credentials
- host filesystem
- unrelated environment variables

Candidate execution must occur in disposable isolation.

Use the strongest practical isolation available.

Document the threat model.

---

# Pre-Execution Inspection

Before any install script or candidate code executes:

Inspect:

- README
- repository metadata
- package manifests
- dependency files
- Dockerfiles
- shell scripts
- install scripts
- CI definitions
- executable binaries
- downloaded assets
- network endpoints
- suspicious encoded content
- privilege requirements

Search for behavior including:

- curl | sh
- wget | sh
- sudo
- chmod on sensitive paths
- credential access
- ~/.ssh
- ~/.aws
- ~/.config
- Docker socket
- /proc inspection
- shell persistence
- cron creation
- startup modification
- shell profile modification
- outbound callbacks
- miners
- tunneling tools
- reverse shells
- destructive filesystem commands

Do not assume absence of these indicators means code is safe.

---

# Sandbox Policy

Candidate execution must use disposable environments with:

- ephemeral filesystem
- no secrets
- CPU limits
- RAM limits
- disk limits
- process limits
- timeout
- controlled network access
- explicit GPU access only when needed
- no privileged containers
- no host Docker socket
- no host filesystem mounts except controlled staged files
- automatic teardown

Prefer default-deny networking after dependencies are installed.

Record network requirements.

---

# Repository Research

Before generating tests, establish what the tool actually claims.

Collect:

- exact repository
- exact commit SHA
- release/tag
- README
- documentation
- license
- supported environments
- hardware requirements
- installation instructions
- examples
- claimed capabilities
- known limitations

Create falsifiable claims.

Bad:

> Great developer experience.

Better:

> README claims installation requires one command and no external services.

Bad:

> Fast inference.

Better:

> README claims generation at 2× realtime on an RTX 4090.

Test claims whenever practical.

---

# Evidence Rule

No material assertion in a WorthIt review may exist solely because an LLM thought it sounded reasonable.

Prefer the chain:

`claim -> test -> run -> evidence -> evaluation -> conclusion`

Evidence may include:

- logs
- exit codes
- screenshots
- generated files
- benchmark data
- resource metrics
- timing
- network traces
- source inspection
- reproducible command output

If evidence is missing:

`UNVERIFIED`

Do not convert unknowns into failures.

Do not convert unknowns into successes.

---

# Test Result States

Use:

- PASS
- PARTIAL
- FAIL
- BLOCKED
- UNVERIFIED
- NOT_APPLICABLE

Keep them distinct.

---

# Daily Testing

Default:

`DAILY_TEST_LIMIT=5`

The daily job should:

1. discover candidates
2. deduplicate them
3. qualify them
4. assign interestingness
5. perform risk screening
6. rank candidates
7. select up to 5
8. research each repository
9. extract claims
10. design tests
11. review the test plan
12. sandbox execution
13. gather evidence
14. evaluate
15. score
16. write reviews
17. publish successful reviews
18. update rankings
19. produce a daily summary

If fewer than five candidates are worth testing, test fewer than five.

**Never fill the quota with garbage.**

Quality beats quota.

---

# Scoring

Maintain separate dimensions.

Default:

- 30% claim verification / functionality
- 20% utility
- 15% setup experience
- 10% reliability
- 10% performance / efficiency
- 5% documentation
- 5% safety / privacy posture
- 5% novelty

Never discard the component scores.

Composite scores must remain explainable.

---

# Bullshit Ratio

WorthIt may calculate a Bullshit Ratio.

It must be evidence-based.

It should reflect important promotional claims that fail testing or materially overstate demonstrated capability.

Do not count:

- untested claims
- claims blocked by unavailable hardware
- subjective marketing language that cannot reasonably be evaluated

Do not manufacture controversy.

---

# Editorial Standard

WorthIt reviews should sound like they were written by a technically competent person who actually used the software.

The writing must be derived from the test evidence.

Prefer:

> The README says setup takes one command. It did. The first successful run took 2 minutes 17 seconds from a clean environment.

Avoid:

> The platform offers a seamless and robust installation experience.

---

# AI Writing Smell Policy

Do not publish stereotypical AI prose.

Avoid:

- "In today's rapidly evolving..."
- "In the ever-changing landscape..."
- "It's worth noting..."
- "It's important to note..."
- "This powerful tool..."
- "This innovative solution..."
- "game-changer"
- "revolutionary"
- "robust" without a concrete meaning
- "seamless" without evidence
- "leverage" when "use" works
- "delve"
- "unlock"
- "empower"
- "boasts"
- "stands out"
- "impressive" without saying why
- "Overall," as a canned conclusion
- "Whether you're a..."
- excessive em dashes
- fake enthusiasm
- repetitive summary paragraphs
- artificial three-item rhetorical lists
- restating the introduction in the conclusion
- inflated adjectives
- unnecessary headings
- generic transitions
- anthropomorphizing the tool
- pretending subjective judgment is measured fact

Do not force slang to sound human.

Do not intentionally add spelling mistakes.

Human writing here means:

- specific
- economical
- evidence-driven
- occasionally opinionated when clearly labeled
- willing to say something failed
- willing to say something is boring
- willing to say something is good
- willing to say there wasn't enough evidence

---

# Review Voice

Preferred voice:

Technical.
Plainspoken.
Curious.
Skeptical.
Fair.
Occasionally dry.
Concise.

Write like someone explaining the result to another engineer.

Do not imitate marketing copy.

Do not attack maintainers.

Critique the software and claims, not the people.

---

# Editorial Fact Check

Before publication, run a separate editorial validation pass.

Verify:

- every number
- every score
- every benchmark
- every failed claim
- every quotation
- every version
- every commit
- every hardware statement
- every installation statement

Then run a second prose review looking specifically for:

- unsupported claims
- exaggerated conclusions
- AI-writing clichés
- repetition
- unnecessary verbosity
- unfair framing

Claude may perform this pass independently.

If editorial review disagrees materially with the evaluator, resolve the disagreement before publication.

---

# Public Blog

WorthIt must publish a public review site.

Prefer a static publishing model for V1.

Use GitHub Pages unless another already-authenticated, clearly superior hosting environment exists.

Do not introduce paid hosting merely to publish V1.

The public site should include:

- homepage
- latest reviews
- daily WorthIt report
- leaderboards
- individual review pages
- repository history
- methodology
- scoring explanation
- security/testing methodology
- About page
- RSS/Atom feed
- sitemap
- robots.txt

Generated reviews should become static content suitable for long-term linking.

---

# Blog Design

Do not make it look like an AI startup landing page.

Avoid:

- giant gradient hero
- glowing blobs
- excessive rounded cards
- fake testimonials
- fake company logos
- "Get Started" funnels
- meaningless statistics
- stock imagery
- chatbot imagery
- excessive animations

Prefer something closer to:

- technical publication
- lab notebook
- software review magazine
- benchmark site

Dense enough to be useful.
Clean enough to read.

A review score should never be more visually prominent than the evidence supporting it.

---

# Homepage Message

The product concept should be immediately understandable.

Something approximately as direct as:

> AI tools tested so you don't have to.

Supporting copy should explain that WorthIt discovers trending projects, runs them in isolated environments, tests their claims, and publishes the evidence.

Do not oversell autonomy.

---

# Blog Content Types

Support:

## Review

A deep evaluation of one repository.

## Daily Hunt

Summary of that day's discovery/testing.

Example:

- 83 discovered
- 21 qualified
- 5 tested
- 4 completed
- 1 blocked

Include:

- best tool
- biggest disappointment
- interesting projects not tested
- reasons for skips

## Retest

What changed between versions.

## Methodology

How WorthIt tests a category.

## Benchmark

Cross-project comparisons when evidence is comparable.

---

# Publication Gate

A review may be automatically published only when:

- repository identity is verified
- exact tested commit is stored
- risk screening completed
- meaningful tests executed
- evidence exists
- score calculation succeeds
- confidence is sufficient
- factual validation succeeds
- editorial validation succeeds

Possible publication decisions:

- PUBLISH
- PUBLISH_WITH_LIMITATIONS
- HOLD_INSUFFICIENT_EVIDENCE
- HOLD_EDITORIAL_REVIEW
- HOLD_SECURITY_REVIEW
- REJECT

Do not publicly publish half-generated reviews.

---

# Corrections

WorthIt will sometimes be wrong.

Every review should record:

- tested version
- tested date
- WorthIt version

Provide a correction mechanism.

Never silently rewrite historical evidence.

If a material error is found:

1. preserve the original evaluation
2. add a correction
3. rerun where appropriate
4. publish the new result
5. explain what changed

---

# Author Fairness

Before making strong negative claims, distinguish:

- project defect
- documentation defect
- environment incompatibility
- WorthIt limitation
- unverified claim

For serious accusations such as malicious behavior, do not publish an LLM inference as fact.

Require concrete evidence and human review.

---

# Deployment

WorthIt's own public site should deploy automatically from the default branch once CI passes.

Prefer:

`GitHub Actions -> static build -> GitHub Pages`

Deployment must require:

- tests passing
- type checking passing
- linting passing
- site build succeeding
- no leaked secrets
- no unpublished test artifacts accidentally included
- editorial validation for newly published reviews

Preview locally before first production deployment.

If repository settings or permissions prevent deployment:

1. configure everything that can be configured in code
2. document the exact remaining manual action
3. continue all other work

Do not replace GitHub Pages with a random hosting provider just to avoid one missing setting.

---

# Secrets

Never commit secrets.

Provide `.env.example`.

Use GitHub Actions secrets for credentials.

Review generated static files for accidental:

- tokens
- filesystem paths
- usernames
- internal IPs
- temporary signed URLs
- sandbox credentials
- environment dumps

before publishing.

---

# CI

CI should eventually validate:

- unit tests
- integration tests
- type checking
- linting
- migrations
- deterministic fake pipeline
- static site generation
- broken links
- secret scanning
- review schema validation

Candidate execution should not run automatically in ordinary pull-request CI.

---

# Human-Facing Product Standard

WorthIt should eventually make this possible:

> I hear about some new AI repo.

Instead of spending an hour figuring out whether it's real, I look it up on WorthIt.

WorthIt tells me:

- what it claims
- what version was tested
- what hardware was used
- what actually worked
- what failed
- how annoying installation was
- what resources it consumed
- how much evidence exists
- whether it's worth my time

That is the product.

---

# Engineering Rule

Do not confuse architecture completeness with product completeness.

A beautiful agent framework that has never successfully tested a real repository is not useful.

Keep driving toward real evaluations.

---

# Definition of Trust

A published WorthIt review should be defensible using:

`claim -> test -> evidence -> score -> prose`

If the prose cannot be traced back through that chain, revise it.

---

# Final Rule

WorthIt exists because people do not have enough time to personally try every interesting AI project.

Do not waste their time with unverified reviews.
