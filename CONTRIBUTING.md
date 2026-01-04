# Contributing to Pittsburgh Bus Tracker

Thanks for your interest in contributing! This document provides guidelines for contributing to the project.

## 🚀 Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/bus-tracker.git
   cd bus-tracker
   ```
3. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-cov flake8  # Dev dependencies
   ```
5. **Set up environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your TrueTime API key
   ```

## 🔧 Development Workflow

### Running the App
```bash
python api.py
```
Open http://localhost:5001 in your browser.

### Running Tests
```bash
pytest tests/ -v
```

### Running Tests with Coverage
```bash
pytest tests/ --cov=. --cov-report=term-missing
```

### Linting
```bash
flake8 api.py --max-line-length=120
```

## 📝 Pull Request Process

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** and ensure:
   - [ ] Tests pass (`pytest tests/`)
   - [ ] Code is linted (`flake8`)
   - [ ] New features have tests
   - [ ] Documentation is updated if needed

3. **Commit your changes**:
   ```bash
   git commit -m "feat: add your feature description"
   ```
   Follow [Conventional Commits](https://www.conventionalcommits.org/) format:
   - `feat:` new feature
   - `fix:` bug fix
   - `docs:` documentation only
   - `refactor:` code refactoring
   - `test:` adding/updating tests
   - `chore:` maintenance tasks

4. **Push and create a PR**:
   ```bash
   git push origin feature/your-feature-name
   ```
   Then open a Pull Request on GitHub.

## 🐛 Reporting Bugs

When reporting bugs, please include:

- **Environment**: OS, Python version, browser
- **Steps to reproduce**: What did you do?
- **Expected behavior**: What should happen?
- **Actual behavior**: What happened instead?
- **Screenshots**: If applicable

## 💡 Feature Requests

We welcome feature ideas! Please open an issue with:

- **Use case**: Why is this feature needed?
- **Proposed solution**: How should it work?
- **Alternatives**: Any other approaches considered?

## 📁 Project Structure

```
bus-tracker/
├── api.py              # Flask API (main entry point)
├── app.js              # Frontend JavaScript
├── index.html          # Frontend HTML
├── style.css           # Frontend styles
├── tests/              # Test suite
│   ├── test_api.py     # Unit tests
│   └── test_integration.py
├── .github/workflows/  # CI/CD
└── requirements.txt    # Python dependencies
```

## 🔒 Security

- Never commit `.env` files or API keys
- Report security vulnerabilities privately via GitHub Security Advisories

## 📄 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Questions? Open an issue or reach out!
