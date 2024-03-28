from fastapi import FastAPI, Form, HTTPException, Depends

from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.requests import Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from datetime import datetime, timedelta
from db import Base, SessionLocal, engine
from model import JobSubmission, enrty
from typing import Optional
from httpx import AsyncClient
from typing import Any, Dict
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
from pydantic import EmailStr
from datetime import datetime

# Define a Pydantic model to represent the response from the external AI service
class AIResponse(BaseModel):
    status_code: int
    response_json: Dict[str, Any]


app = FastAPI()
PRICING_TIERS = {"basic": 100, "standard": 250, "premium": 500}
templates = Jinja2Templates(directory="templates")
Base.metadata.create_all(engine)
SECRET_KEY = "ewirjoiewrfnkdshfrewjrlw"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
app.mount("/static", StaticFiles(directory="D:/PYTHONAPI/templates"), name="static")


# Route to serve the index.html file from the root path
@app.get("/client", response_class=HTMLResponse)
async def get_index():
    return FileResponse("D:/PYTHONAPI/templates/client.html")


# Generate JWT token
def create_access_token(email: str):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": email, "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


# Verify password
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


# Route to display the login form
@app.get("/", response_class=HTMLResponse)
async def show_login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# Route to handle user login
@app.post("/login/")
async def login(request: Request, email: EmailStr = Form(...), password: str = Form(...)):

    # Establish a database session
    with SessionLocal() as db:
        # Query the database for the user
        user = db.query(enrty).filter(enrty.email == email).first()

        # Check if user exists and password is correct
        if not user or not verify_password(password, user.password):
            raise HTTPException(status_code=401, detail="Incorrect email or password")

        # Generate access token
        token = create_access_token(email)

    # Return the access token
    return JSONResponse(content={"access_token": token, "token_type": "bearer"})


# Route to handle user signup
@app.post("/signup/")
async def signup(
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    pricing_tier: str = Form(...)
):
    credits = PRICING_TIERS[pricing_tier]
    hashed_password = pwd_context.hash(password)
    new_user = enrty(
        email=email, password=hashed_password, full_name=full_name, credits=credits
    )
    db = SessionLocal()
    db.add(new_user)
    db.commit()
    db.close()
    return {"message": "Sign up successful"}


def deduct_credits(user_id, amount_to_deduct):
    user = SessionLocal().query(enrty).get(user_id)
    if user:
        if user.credits >= amount_to_deduct:
            user.credits -= amount_to_deduct
            SessionLocal().commit()
            return True
    return False


def has_sufficient_credits(user_id, required_credits):
    user = SessionLocal().query(enrty).get(user_id)
    return user.credits >= required_credits if user else False


def add_credits(user_id, amount_to_add):
    user = SessionLocal().query(enrty).get(user_id)
    if user:
        user.credits += amount_to_add
        SessionLocal().commit()


from fastapi import BackgroundTasks

# Define a queue to hold pending job submissions
job_queue = []


def get_user_by_email(email: str, db: Session = Depends(SessionLocal)):
    user = db.query(enrty).filter(enrty.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


# Route to submit a job
@app.post("/submit_job/")
async def submit_job(
    request: Request,
    email: str = Form(...),  # Assume email is provided in the form
    job_title: str = Form(...),
    description: str = Form(...),
    deadline: str = Form(...),  # Change parameter name to "deadline"
    priority: str = Form(...),
):
    db = SessionLocal()

    # Get user by email
    user = get_user_by_email(email, db)

    # Check if user has sufficient credits
    if user.credits <= 0:
        db.close()
        raise HTTPException(status_code=403, detail="Insufficient credits")

    # Deduct credits
    user.credits -= 1

    # Convert deadline string to datetime object
    deadline_dt = datetime.fromisoformat(deadline)

    # Create job submission
    job_submission = JobSubmission(
        user_id=user.id,
        job_title=job_title,
        description=description,
        deadline=deadline_dt,  # Use the converted deadline datetime object
        priority=priority,
    )
    db.add(job_submission)
    db.commit()
    db.close()
    return {"message": "Job submitted successfully"}


# External API configuration (replace these with actual values)
API_URL = "https://api.openai.com/v1/engines/davinci/completions"
API_KEY = "sk-1234567890abcdef1234567890abcdef"  # Your OpenAI API key


# Define a Pydantic model to represent the request body for the external API
class AIRequest(BaseModel):
    prompt: str


# Function to make the API call to the external AI service
async def call_external_ai_service(request: AIRequest):
    async with AsyncClient() as client:
        headers = {"Authorization": f"Bearer {API_KEY}"}
        response = await client.post(
            f"{API_URL}", headers=headers, json={"prompt": request.prompt}
        )
        return response


# Endpoint to handle the API request to the external AI service
@app.get("/complete", include_in_schema=False)
@app.post("/complete/")
async def complete_text(
    request: AIRequest, response: AIResponse = Depends(call_external_ai_service)
):
    if response.status_code == 200:
        # Handle successful response from the external AI service
        ai_result = response.response_json
        return {"result": ai_result}
    elif response.status_code == 401:
        # Unauthorized error
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API key")
    elif response.status_code == 422:
        # Unprocessable Entity error
        raise HTTPException(
            status_code=422, detail="Unprocessable Entity: Invalid request"
        )
    else:
        # Other errors
        raise HTTPException(
            status_code=response.status_code, detail="Error from external AI service"
        )


async def complete_endpoint(request: Request):
    # Handle GET requests to both /complete and /complete/ here
    return {"message": "This is the complete endpoint"}


# Define a separate GET endpoint for /complete/
@app.get("/complete/", include_in_schema=False)
async def complete_with_trailing_slash(request: Request):
    # Handle GET requests to /complete/ here
    return {"message": "This is the complete endpoint with trailing slash"}


def process_job(user_id, job_details, credits_required):
    if not deduct_credits(user_id, credits_required):
        raise HTTPException(status_code=500, detail="Failed to deduct credits")

    # Process the job here

    # You can also add additional logic to handle job processing, such as sending emails, etc.

    # Example: Print job details
    print(f"Job submitted by user {user_id}: {job_details}")

    # After processing the job, you might want to check the queue for pending jobs
    process_pending_jobs()


def process_pending_jobs():
    while job_queue:
        user_id, job_details, credits_required = job_queue.pop(0)
        if has_sufficient_credits(user_id, credits_required):
            # Process the pending job if user now has sufficient credits
            process_job(user_id, job_details, credits_required)
        else:
            # Put the job back in the queue if user still does not have sufficient credits
            job_queue.append((user_id, job_details, credits_required))


@app.get("/pricing", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("pricing.html", {"request": request})


# Route to display the signup form
@app.get("/signup/", response_class=HTMLResponse)
async def show_signup_form(request: Request):
    return templates.TemplateResponse("SignUp.html", {"request": request})


@app.get("/job/", response_class=HTMLResponse)
async def show_job_form(request: Request):
    return templates.TemplateResponse("job.html", {"request": request})
