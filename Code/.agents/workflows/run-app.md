---
description: How to run the CardioReport application
---

## Prerequisites

// turbo-all

1. Install Python dependencies:

```bash
cd /Users/heetbarot/Documents/Cardio-io/Code && pip3 install -r requirements.txt
```

2. Start the backend server:

```bash
cd /Users/heetbarot/Documents/Cardio-io/Code && uvicorn backend.main:app --reload --port 8000
```

3. In a separate terminal, start the frontend dev server:

```bash
cd /Users/heetbarot/Documents/Cardio-io/Code/frontend && npx -y serve -s . -l 3000
```

4. Open the app at http://localhost:3000
