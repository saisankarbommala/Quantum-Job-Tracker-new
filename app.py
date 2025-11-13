# [file name]: app.py
# [file content begin]
import os
import json
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from cryptography.fernet import Fernet
import hashlib

# Qiskit / IBM Runtime
from qiskit import QuantumCircuit, transpile
from qiskit_ibm_runtime import QiskitRuntimeService, Sampler, Session

# Import utility modules
from utils.queue_predictor import QueuePredictor
from utils.backend_recommender import BackendRecommender
from utils.notification_manager import NotificationManager
from utils.historical_analyzer import HistoricalAnalyzer
from utils.backend_data_fetcher import BackendDataFetcher

# Optional local simulator
try:
    from qiskit_aer import AerSimulator

    AER_AVAILABLE = True
except Exception:
    AER_AVAILABLE = False

# -----------------------------
# Flask setup
# -----------------------------
app = Flask(__name__)


# Configuration
class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", Fernet.generate_key().decode())
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = os.environ.get("FLASK_ENV") == 'production'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)
    ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", Fernet.generate_key().decode())
    NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "")
    NOTIFICATION_SLACK_WEBHOOK = os.environ.get("NOTIFICATION_SLACK_WEBHOOK", "")
    POLLING_INTERVAL = int(os.environ.get("POLLING_INTERVAL", 300))  # 5 minutes


app.config.from_object(Config)

# Rate limiting
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Encryption setup
cipher_suite = Fernet(app.config['ENCRYPTION_KEY'])

# Initialize utility modules
queue_predictor = QueuePredictor()
backend_recommender = BackendRecommender()
notification_manager = NotificationManager(
    app.config['NOTIFICATION_EMAIL'],
    app.config['NOTIFICATION_SLACK_WEBHOOK']
)
historical_analyzer = HistoricalAnalyzer()

# Global storage for user-specific data
user_services = {}
user_backends = {}
user_jobs = {}
last_update_time = datetime.now()


def encrypt_token(token):
    return cipher_suite.encrypt(token.encode())


def decrypt_token(encrypted_token):
    return cipher_suite.decrypt(encrypted_token).decode()


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# Authentication decorator
def token_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("token"):
            flash("Please authenticate with your IBM Quantum token", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return decorated_function


# -----------------------------
# Helpers
# -----------------------------
def get_services_for_user():
    """
    Build public and optional private services using session credentials.
    Returns: (public_service, private_service, error_message)
    """
    encrypted_token = session.get("token")
    crn = session.get("crn")
    user_id = session.get("user_id")

    if not encrypted_token:
        return None, None, "No authentication token found"

    # Check if we already have services for this user
    if user_id and user_id in user_services:
        return user_services[user_id]

    try:
        token = decrypt_token(encrypted_token)
    except Exception:
        return None, None, "Invalid token format"

    public_service = None
    private_service = None
    error_msg = None

    # Public: IBM Quantum Platform
    try:
        public_service = QiskitRuntimeService(channel="ibm_quantum_platform", token=token)
        user_name = list(public_service.instances()[0].values())[2].title()
        session['user_name'] = user_name
    except Exception as e:
        error_msg = f"Public service error: {str(e)}"

    # Private: IBM Cloud via CRN
    if crn:
        try:
            private_service = QiskitRuntimeService(channel="ibm_cloud", instance=crn, token=token)
        except Exception as e:
            error_msg = f"{error_msg} | Private service error: {str(e)}" if error_msg else f"Private service error: {str(e)}"
            private_service = None

    # Store services for this user
    if user_id:
        user_services[user_id] = (public_service, private_service, error_msg)

    return public_service, private_service, error_msg


def get_backends_for_user(public_service, private_service):
    """Get backends for a user with proper error handling"""
    user_id = session.get("user_id")
    backends = []

    # Public backends
    if public_service:
        try:
            for backend in public_service.backends():
                status_info = get_backend_status(backend)
                backend_info = {
                    "name": backend.name,
                    "qubits": status_info.get("qubits", "Unknown"),
                    "queue": status_info.get("pending_jobs", "Unknown"),
                    "status": "✅ Available" if status_info.get("operational", False) else "❌ Down",
                    "status_msg": status_info.get("status_msg", ""),
                    "service_type": "public",
                    "simulator": getattr(backend.configuration(), 'simulator', False)
                }
                backends.append(backend_info)
        except Exception as e:
            print('backend error', e)

    # Private backends
    if private_service:
        try:
            for backend in private_service.backends():
                status_info = get_backend_status(backend)
                backend_info = {
                    "name": backend.name,
                    "qubits": status_info.get("qubits", "Unknown"),
                    "queue": status_info.get("pending_jobs", "Unknown"),
                    "status": "✅ Available" if status_info.get("operational", False) else "❌ Down",
                    "status_msg": status_info.get("status_msg", ""),
                    "service_type": "private",
                    "simulator": getattr(backend.configuration(), 'simulator', False)
                }
                backends.append(backend_info)
        except Exception as e:
            print(f"Error loading private backends for user {user_id}: {e}")

    # Store backends for this user
    if user_id:
        user_backends[user_id] = backends

    return backends


def get_jobs_for_user(public_service, private_service, limit=20):
    """Get jobs for a user with proper error handling"""
    user_id = session.get("user_id")
    all_jobs = []

    # Public jobs
    if public_service:
        try:
            public_jobs = list(public_service.jobs(limit=limit))
            all_jobs.extend(public_jobs)
            print(f"Found {len(public_jobs)} public jobs for user {user_id}")
        except Exception as e:
            print(f"Error loading public jobs for user {user_id}: {e}")

    # Private jobs
    if private_service:
        try:
            private_jobs = list(private_service.jobs(limit=limit))
            all_jobs.extend(private_jobs)
            print(f"Found {len(private_jobs)} private jobs for user {user_id}")
        except Exception as e:
            print(f"Error loading private jobs for user {user_id}: {e}")

    # Process jobs for display
    processed_jobs = [process_job_for_display(job) for job in all_jobs]
    processed_jobs.sort(key=lambda x: x["creation_time"], reverse=True)

    # Store jobs for this user
    if user_id:
        user_jobs[user_id] = processed_jobs

    return processed_jobs


from qiskit_aer import AerSimulator
from qiskit import QuantumCircuit, transpile
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
import os


def submit_quantum_job_open_plan(backend, circuit_type="single_qubit", shots=100, tags=None):
    """
    Submit quantum job using backend.run() or SamplerV2 for local simulators.
    """
    # Create quantum circuit based on type
    print("_________________________Open_____________________________________")
    """
        Submit a simple quantum job to the given backend using SamplerV2.
        """

    # 1. Create circuit
    if circuit_type == "bell":
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.cx(0, 1)
        qc.measure_all()
    elif circuit_type == "ghz":
        qc = QuantumCircuit(3)
        qc.h(0)
        qc.cx(0, 1)
        qc.cx(0, 2)
        qc.measure_all()
    else:  # single_qubit
        qc = QuantumCircuit(1)
        qc.h(0)
        qc.measure_all()

    # 2. Transpile for the backend
    qc_t = transpile(qc, backend)

    # 3. Run with SamplerV2
    sampler = SamplerV2(mode=backend)
    job = sampler.run([qc_t], shots=shots)

    print(f"Job submitted to {backend.name} with ID: {job.job_id()}")
    return job


def submit_estimator_job(backend, circuit_type="single_qubit", shots=100, tags=None):
    """
    Submit job using Estimator for expectation value calculations without sessions
    for Open Plan compatibility
    """
    # Create quantum circuit based on type
    print("_________________________Estimator_____________________________________")
    if circuit_type == "bell":
        qc = QuantumCircuit(2)
        qc.h(0)
        qc.cx(0, 1)
        # For estimator, we typically don't measure all qubits
    elif circuit_type == "ghz":
        qc = QuantumCircuit(3)
        qc.h(0)
        qc.cx(0, 1)
        qc.cx(0, 2)
    else:  # single_qubit (default)
        qc = QuantumCircuit(1)
        qc.h(0)

    # Define observables (Pauli strings for expectation values)
    if circuit_type == "bell":
        # ZZ observable for Bell state
        from qiskit.quantum_info import SparsePauliOp
        observable = SparsePauliOp("ZZ")
    elif circuit_type == "ghz":
        # ZZZ observable for GHZ state
        from qiskit.quantum_info import SparsePauliOp
        observable = SparsePauliOp("ZZZ")
    else:  # single_qubit
        # Z observable for single qubit
        from qiskit.quantum_info import SparsePauliOp
        observable = SparsePauliOp("Z")

    # Transpile for the specific backend
    qc_t = transpile(qc, backend)

    try:
        # Use QiskitRuntimeService to submit estimator job directly
        public_service, private_service, _ = get_services_for_user()

        # Use the correct service based on backend
        service_to_use = None
        if hasattr(backend, 'channel') and backend.channel == "ibm_cloud":
            service_to_use = private_service
        else:
            service_to_use = public_service

        if not service_to_use:
            raise Exception("No valid service available for Estimator")

        # Submit estimator job directly through the service
        job = service_to_use.run(
            program_id="estimator",
            inputs={
                "circuits": qc_t,
                "observables": observable,
                "shots": shots
            },
            backend=backend.name
        )

        # Add tags if provided
        if tags:
            try:
                job.update_tags(tags)
            except Exception as e:
                print(f"Error adding tags to estimator job: {e}")

        # Record job submission in history
        try:
            historical_analyzer.record_job_event(
                job.job_id(),
                "submitted_estimator",
                data={
                    "backend": backend.name,
                    "circuit_type": circuit_type,
                    "shots": shots,
                    "observable": str(observable),
                    "job_type": "estimator"
                }
            )
        except Exception as e:
            print(f"Error recording estimator job event: {e}")

        return job

    except Exception as e:
        print(f"Error submitting estimator job: {e}")

        # Fallback: Try using sampler mode with measurements
        try:
            print("Attempting fallback to sampler mode with measurements...")

            # Create a circuit with measurements for fallback
            if circuit_type == "bell":
                qc_fallback = QuantumCircuit(2)
                qc_fallback.h(0)
                qc_fallback.cx(0, 1)
                qc_fallback.measure_all()
            elif circuit_type == "ghz":
                qc_fallback = QuantumCircuit(3)
                qc_fallback.h(0)
                qc_fallback.cx(0, 1)
                qc_fallback.cx(0, 2)
                qc_fallback.measure_all()
            else:
                qc_fallback = QuantumCircuit(1)
                qc_fallback.h(0)
                qc_fallback.measure_all()

            qc_fallback_t = transpile(qc_fallback, backend)

            # Use the primary method
            job = backend.run(qc_fallback_t, shots=shots)

            if tags:
                try:
                    job.update_tags(tags)
                except Exception as tag_error:
                    print(f"Error adding tags in fallback: {tag_error}")

            try:
                historical_analyzer.record_job_event(
                    job.job_id(),
                    "submitted_estimator_fallback",
                    data={
                        "backend": backend.name,
                        "circuit_type": circuit_type,
                        "shots": shots,
                        "job_type": "sampler_fallback"
                    }
                )
            except Exception as history_error:
                print(f"Error recording fallback job event: {history_error}")

            return job

        except Exception as fallback_error:
            print(f"Estimator fallback also failed: {fallback_error}")
            raise Exception(f"Failed to submit estimator job using both methods: {fallback_error}")


def get_backend_status(backend):
    """Get backend status information with enhanced error handling"""
    try:
        status = backend.status()
        config = backend.configuration()
        return {
            "operational": status.operational,
            "pending_jobs": status.pending_jobs,
            "status_msg": status.status_msg,
            "qubits": config.n_qubits,
            "backend_version": config.backend_version,
            "max_shots": getattr(config, 'max_shots', 'Unknown'),
            "max_experiments": getattr(config, 'max_experiments', 'Unknown'),
            "simulator": getattr(config, 'simulator', False),
        }
    except Exception as e:
        print(f"Error getting backend status for {backend.name}: {e}")
        return {
            "operational": False,
            "pending_jobs": "Unknown",
            "status_msg": f"Error: {str(e)}",
            "qubits": "Unknown",
            "backend_version": "Unknown",
            "error": str(e)
        }


def process_job_for_display(job):
    """Convert job object to display-friendly format"""
    try:
        backend_name = job.backend().name if job.backend() else "Unknown"
    except Exception:
        backend_name = "Unknown"

    try:
        status = job.status().name
    except Exception:
        try:
            status = str(job.status())
        except Exception:
            status = "Unknown"

    try:
        created = job.creation_date.strftime("%Y-%m-%d %H:%M:%S") if job.creation_date else "Unknown"
    except Exception:
        created = "Unknown"

    try:
        tags = getattr(job, 'tags', [])
    except Exception:
        tags = []

    try:
        program_id = getattr(job, 'program_id', 'Unknown')
    except Exception:
        program_id = 'Unknown'

    # Get user ID from tags or session
    user_id = get_job_user_id(job)

    # Get estimated wait time for queued jobs
    estimated_start = None
    if status in ['QUEUED', 'VALIDATING']:
        try:
            backend = job.backend()
            if backend:
                status_info = get_backend_status(backend)
                estimated_start = queue_predictor.estimate_start_time(
                    job.job_id(), backend.name, status_info.get('pending_jobs', 0)
                )
        except Exception:
            pass

    return {
        "job_id": job.job_id(),
        "backend": backend_name,
        "status": status,
        "creation_time": created,
        "tags": tags,
        "program_id": program_id,
        "status_class": get_job_status_class(status),
        "estimated_start": estimated_start,
        "user_id": user_id
    }


def get_job_user_id(job):
    """Extract user ID from job tags or session"""
    try:
        tags = getattr(job, 'tags', [])
        for tag in tags:
            if tag.startswith('user:'):
                return tag.split(':', 1)[1]
    except Exception:
        pass

    # Fallback to session user ID
    return session.get('user_id', 'unknown')


def get_job_status_class(status):
    """Return CSS class based on job status"""
    status = str(status).lower()
    if status in ['completed', 'done']:
        return 'success'
    elif status in ['running', 'queued', 'validating']:
        return 'warning'
    elif status in ['error', 'failed', 'cancelled']:
        return 'danger'
    else:
        return 'secondary'


def filter_jobs(jobs, filters):
    """Filter jobs based on criteria"""
    filtered_jobs = jobs

    if filters.get('user'):
        filtered_jobs = [job for job in filtered_jobs if job.get('user_id') == filters['user']]

    if filters.get('backend'):
        filtered_jobs = [job for job in filtered_jobs if job.get('backend') == filters['backend']]

    if filters.get('status'):
        filtered_jobs = [job for job in filtered_jobs if job.get('status') == filters['status']]

    if filters.get('tags'):
        tag_filter = filters['tags'].split(',')
        filtered_jobs = [job for job in filtered_jobs if any(tag in job.get('tags', []) for tag in tag_filter)]

    return filtered_jobs


# Background polling for user data refresh
def background_user_data_refresh():
    """Background thread to refresh user data periodically"""
    while True:
        try:
            time.sleep(app.config['POLLING_INTERVAL'])

            # Refresh data for all logged-in users
            for user_id, (public_service, private_service, _) in list(user_services.items()):
                try:
                    # Refresh backends
                    get_backends_for_user(public_service, private_service)

                    # Refresh jobs
                    get_jobs_for_user(public_service, private_service)

                    print(f"Refreshed data for user {user_id}")
                except Exception as e:
                    print(f"Error refreshing data for user {user_id}: {e}")

        except Exception as e:
            print(f"Background refresh error: {e}")
            time.sleep(300)  # Wait 5 minutes on error


# Start background thread
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    refresh_thread = threading.Thread(target=background_user_data_refresh, daemon=True)
    refresh_thread.start()


# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    """Login page with IBM Quantum token authentication"""
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        crn = request.form.get("crn", "").strip()

        if not token:
            flash("IBM Quantum token is required", "danger")
            return redirect(url_for("login"))

        try:
            # Validate token by attempting to create a service instance
            test_service = QiskitRuntimeService(channel="ibm_quantum_platform", token=token)
            # Test connection by getting backends list
            backends = list(test_service.backends()[:3])
            print(f"Successfully authenticated. Found {len(backends)} backends.")
        except Exception as e:
            flash(f"❌ Authentication failed: {str(e)}", "danger")
            print(f"Authentication failed: {e}")
            return redirect(url_for("login"))

        # Save encrypted credentials to session
        session["token"] = encrypt_token(token)
        user_id = hash_token(token)
        session["user_id"] = user_id

        if crn:
            session["crn"] = crn
            flash("✅ Authenticated with private CRN", "success")
        else:
            session.pop("crn", None)
            flash("✅ Authenticated with public access", "success")

        return redirect(url_for("dashboard"))

    return render_template("login.html", title="Login")


@app.route("/logout")
def logout():
    """Clear session and logout"""
    user_id = session.get("user_id")
    if user_id:
        # Clean up user data
        user_services.pop(user_id, None)
        user_backends.pop(user_id, None)
        user_jobs.pop(user_id, None)

    session.clear()
    flash("Logged out successfully", "info")
    return redirect(url_for("login"))


@app.route("/dashboard", methods=["GET", "POST"])
@token_required
@limiter.limit("10 per minute")
def dashboard():
    """Main dashboard with backend status and job submission"""
    public_service, private_service, service_error = get_services_for_user()

    if service_error:
        flash(f"Service error: {service_error}", "warning")

    # Handle job submission
    if request.method == "POST":
        backend_name = request.form.get("backend", "").strip()
        use_private = request.form.get("use_private", "false").lower() == "true"
        use_aer = request.form.get("use_aer", "false").lower() == "true"
        use_estimator = request.form.get("use_estimator", "false").lower() == "true"  # NEW: Est
        circuit_type = request.form.get("circuit_type", "single_qubit")
        shots = int(request.form.get("shots", 100))
        tags = request.form.get("tags", "").strip()
        priority = request.form.get("priority", "normal")

        try:
            # Local simulation
            if use_aer:
                if not AER_AVAILABLE:
                    raise RuntimeError("AerSimulator not available")

                sim = AerSimulator()
                if circuit_type == "bell":
                    qc = QuantumCircuit(2)
                    qc.h(0)
                    qc.cx(0, 1)
                    qc.measure_all()
                elif circuit_type == "ghz":
                    qc = QuantumCircuit(3)
                    qc.h(0)
                    qc.cx(0, 1)
                    qc.cx(0, 2)
                    qc.measure_all()
                else:
                    qc = QuantumCircuit(1)
                    qc.h(0)
                    qc.measure_all()

                qc_t = transpile(qc, sim)
                result = sim.run(qc_t, shots=shots).result()
                counts = result.get_counts()
                flash(f"✅ Local simulation complete | Counts: {counts}", "success")
                return redirect(url_for("dashboard"))

            # Quantum hardware execution
            if not backend_name:
                raise ValueError("Please select a quantum backend")

            # Get the appropriate backend
            if use_private and private_service:
                backend = private_service.backend(backend_name)
                service_type = "private"
            else:
                backend = public_service.backend(backend_name)
                service_type = "public"

            # Prepare tags
            job_tags = []
            if tags:
                job_tags.extend([tag.strip() for tag in tags.split(',')])

            # Add user identifier tag
            user_id = session.get("user_id")
            if user_id:
                job_tags.append(f"user:{user_id}")

            # Add priority tag
            job_tags.append(f"priority:{priority}")

            # NEW: Submit using estimator mode if selected
            if use_estimator:
                job = submit_estimator_job(backend, circuit_type, shots, job_tags)
                job_type = "estimator"
            else:
                # Use the standard sampler/open-plan method
                job = submit_quantum_job_open_plan(backend, circuit_type, shots, job_tags)
                job_type = "sampler"

            # Send notification
            try:
                notification_manager.send_notification(
                    user_id,
                    f"Job Submitted: {job.job_id()}",
                    f"{job_type.capitalize()} job submitted to {backend.name} with {shots} shots",
                    {
                        "job_id": job.job_id(),
                        "backend": backend.name,
                        "status": "SUBMITTED",
                        "job_type": job_type
                    }
                )
            except Exception as e:
                print(f"Error sending notification: {e}")

            flash(f"✅ {job_type.capitalize()} Job submitted to {backend.name} | Job ID: {job.job_id()}", "success")

        except Exception as e:
            flash(f"❌ Error submitting job: {str(e)}", "danger")
            print(f"Job submission error: {e}")

        return redirect(url_for("dashboard"))

    # GET: Prepare backend information
    user_id = session.get("user_id")
    backends = []

    if user_id and user_id in user_backends:
        # Use cached backends if available
        backends = user_backends[user_id]
    else:
        # Fetch fresh backends
        backends = get_backends_for_user(public_service, private_service)

    # Separate public and private backends
    public_backends = [b["name"] for b in backends if b["service_type"] == "public"]
    private_backends = [b["name"] for b in backends if b["service_type"] == "public"]
    public_rows = [b for b in backends if b["service_type"] == "public"]
    private_rows = [b for b in backends if b["service_type"] == "private"]

    # Find least busy backend
    least_busy = None
    try:
        if public_service:
            available_backends = [b for b in public_service.backends() if b.status().operational]
            if available_backends:
                lb = min(available_backends, key=lambda x: x.status().pending_jobs)
                status_info = get_backend_status(lb)
                least_busy = {
                    "name": lb.name,
                    "qubits": status_info.get("qubits", "Unknown"),
                    "queue": status_info.get("pending_jobs", "Unknown"),
                }
    except Exception as e:
        print(f"Error finding least busy backend: {e}")

    # Get backend recommendations
    recommendations = []
    try:
        user_id = session.get("user_id")
        if user_id:
            # Create backend status dict for recommendations
            backend_status_dict = {}
            for backend in backends:
                backend_status_dict[backend["name"]] = {
                    "operational": "Available" in backend["status"],
                    "pending_jobs": backend["queue"] if backend["queue"] != "Unknown" else 0,
                    "qubits": backend["qubits"],
                    "service_type": backend["service_type"],
                    "simulator": backend.get("simulator", False)
                }

            recommendations = backend_recommender.get_recommendations(
                user_id, backend_status_dict
            )
    except Exception as e:
        print(f"Error getting backend recommendations: {e}")

    for backend in backends:
        backend["queue_percentage"] = min(backend["queue"] * 10, 100)

    return render_template(
        "index.html",
        title="Quantum Dashboard",
        public_backends=public_backends,
        private_backends=private_backends,
        public_rows=public_rows,
        private_rows=private_rows,
        recommendations=recommendations,
        least_busy=least_busy,
        aer_available=AER_AVAILABLE,
        service_error=service_error,
        last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@app.route("/jobs", methods=["GET", "POST"])
@token_required
@limiter.limit("20 per minute")
def jobs():
    """Display recent quantum jobs with filtering options"""
    public_service, private_service, service_error = get_services_for_user()

    # Handle CRN updates
    if request.method == "POST":
        crn_val = request.form.get("crn", "").strip()
        if crn_val:
            session["crn"] = crn_val
            flash("✅ CRN updated successfully", "success")
        else:
            session.pop("crn", None)
            flash("ℹ CRN removed", "info")
        return redirect(url_for("jobs"))

    # Apply filters
    filters = {
        'user': request.args.get('user', ''),
        'backend': request.args.get('backend', ''),
        'status': request.args.get('status', ''),
        'tags': request.args.get('tags', '')
    }

    # Get jobs for user
    user_id = session.get("user_id")
    if user_id and user_id in user_jobs:
        # Use cached jobs if available
        all_jobs = user_jobs[user_id]
    else:
        # Fetch fresh jobs
        all_jobs = get_jobs_for_user(public_service, private_service)

    # Apply filters
    filtered_jobs = filter_jobs(all_jobs, filters)

    # Get backends for filter dropdown
    backends = []
    if user_id and user_id in user_backends:
        backends = [b["name"] for b in user_backends[user_id]]
    else:
        backends = get_backends_for_user(public_service, private_service)
        backends = [b["name"] for b in backends]

    return render_template(
        "jobs.html",
        title="Quantum Jobs",
        jobs=filtered_jobs,
        error=service_error,
        current_crn=session.get("crn"),
        has_private_service=private_service is not None,
        total_jobs=len(filtered_jobs),
        filters=filters,
        current_user_id=user_id,
        backends=backends,
        last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


@app.route("/backends")
@token_required
@limiter.limit("20 per minute")
def backends():
    """Backend status and queue visualization"""
    public_service, private_service, service_error = get_services_for_user()

    # Get backends for user
    user_id = session.get("user_id")
    if user_id and user_id in user_backends:
        backend_data = user_backends[user_id]
    else:
        backend_data = get_backends_for_user(public_service, private_service)

    # Add estimated wait times
    for backend in backend_data:
        if backend["queue"] != "Unknown":
            backend["estimated_wait"] = queue_predictor.estimate_average_wait(backend["name"])
        else:
            backend["estimated_wait"] = "Unknown"

    # Sort by queue length
    backend_data.sort(key=lambda x: x['queue'] if x['queue'] != "Unknown" else 9999)

    for backend in backend_data:
        if backend["queue"] != "Unknown" and isinstance(backend["queue"], int):
            backend["queue_percentage"] = min(backend["queue"] * 5, 100)
        else:
            backend["queue_percentage"] = 0

    return render_template(
        "backends.html",
        title="Backend Status",
        backends=backend_data,
        last_update=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )


# [file name]: app.py (alternative approach)
# [file content begin] (alternative analytics route)
@app.route("/analytics")
@token_required
@limiter.limit("20 per minute")
def analytics():
    """Historical analytics and insights"""
    user_id = session.get("user_id")

    # Get jobs directly from the jobs page data
    if user_id and user_id in user_jobs:
        jobs = user_jobs[user_id]
    else:
        # Fallback to fetching from services
        public_service, private_service, service_error = get_services_for_user()
        jobs = get_jobs_for_user(public_service, private_service)

    # Calculate analytics from the jobs
    if jobs:
        total_jobs = len(jobs)
        success_count = sum(1 for j in jobs if j["status"].upper() in ["DONE", "COMPLETED"])
        success_rate = (success_count / total_jobs * 100) if total_jobs > 0 else 0.0

        jobs_by_status = {}
        for job in jobs:
            status = job["status"].upper()
            jobs_by_status[status] = jobs_by_status.get(status, 0) + 1

        jobs_by_backend = {}
        for job in jobs:
            backend = job["backend"]
            if backend != "Unknown":
                jobs_by_backend[backend] = jobs_by_backend.get(backend, 0) + 1

        analytics_data = {
            "total_jobs": total_jobs,
            "success_rate": success_rate,
            "average_queue_time": 0,  # Placeholder
            "jobs_by_status": jobs_by_status,
            "jobs_by_backend": jobs_by_backend,
            "recent_activity": [
                {
                    "job_id": j["job_id"],
                    "last_event": j["status"],
                    "timestamp": j["creation_time"],
                }
                for j in jobs[:10]
            ],
        }
    else:
        # Fallback to historical analyzer
        analytics_data = historical_analyzer.get_analytics(user_id)
        if not analytics_data:
            analytics_data = {
                "total_jobs": 0,
                "success_rate": 0.0,
                "average_queue_time": 0,
                "jobs_by_status": {},
                "jobs_by_backend": {},
                "recent_activity": []
            }

    return render_template(
        "analytics.html",
        title="Analytics & Insights",
        analytics=analytics_data,
        user_id=user_id,
    )


# [file content end] (alternative analytics route)



@app.route("/notifications")
@token_required
@limiter.limit("20 per minute")
def notifications():
    """Notification settings and history"""
    user_id = session.get("user_id")

    # Get user notifications
    user_notifications = notification_manager.get_user_notifications(user_id)

    return render_template(
        "notifications.html",
        title="Notifications",
        notifications=user_notifications,
        user_id=user_id
    )


@app.route("/jobs/<job_id>")
@token_required
@limiter.limit("20 per minute")
def job_details(job_id):
    """Detailed view of a specific quantum job"""
    public_service, private_service, service_error = get_services_for_user()

    job = None
    service_type = None

    # Search for job in public service
    if public_service:
        try:
            job = public_service.job(job_id)
            service_type = "public"
        except Exception as e:
            print(f"Error getting public job {job_id}: {e}")

    # Search for job in private service
    if not job and private_service:
        try:
            job = private_service.job(job_id)
            service_type = "private"
        except Exception as e:
            print(f"Error getting private job {job_id}: {e}")

    if not job:
        flash(f"Job '{job_id}' not found", "danger")
        return redirect(url_for("jobs"))

    # Prepare job information
    job_info = process_job_for_display(job)
    job_info["service_type"] = service_type

    # Get job results if available
    result_data = None
    if job_info["status"].lower() in ['completed', 'done']:
        try:
            result = job.result()
            if hasattr(result, 'get_counts'):
                result_data = {"counts": result.get_counts()}
            else:
                result_data = {"raw": str(result)[:500]}  # Limit size
        except Exception as e:
            result_data = {"error": f"Could not retrieve results: {str(e)}"}

    # Get job timeline
    timeline = historical_analyzer.get_job_timeline(job_id)

    return render_template(
        "job_details.html",
        title=f"Job {job_id}",
        job=job_info,
        result=result_data,
        timeline=timeline
    )


@app.route("/jobs/<job_id>/cancel", methods=["POST"])
@token_required
@limiter.limit("10 per minute")
def cancel_job(job_id):
    """Cancel a running quantum job"""
    public_service, private_service, service_error = get_services_for_user()

    try:
        # Find the job
        job = None
        if public_service:
            try:
                job = public_service.job(job_id)
            except Exception:
                pass

        if not job and private_service:
            try:
                job = private_service.job(job_id)
            except Exception:
                pass

        if not job:
            return jsonify({"success": False, "error": "Job not found"}), 404

        # Cancel the job
        cancellation_result = job.cancel()

        # Send notification
        user_id = session.get("user_id")
        if user_id:
            try:
                notification_manager.send_notification(
                    user_id,
                    f"Job Cancelled: {job_id}",
                    f"Job cancellation requested",
                    {"job_id": job_id, "status": "CANCELLATION_REQUESTED"}
                )
            except Exception as e:
                print(f"Error sending cancellation notification: {e}")

        return jsonify({
            "success": True,
            "message": f"Job {job_id} cancellation requested",
            "cancellation_result": str(cancellation_result)
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/update_crn", methods=["POST"])
@token_required
@limiter.limit("10 per minute")
def update_crn():
    """Update the private instance CRN"""
    crn_val = request.form.get("crn", "").strip()
    if crn_val:
        session["crn"] = crn_val
        flash("✅ CRN updated successfully", "success")
    else:
        session.pop("crn", None)
        flash("ℹ CRN removed", "info")

    # Clear cached services to force reconnection
    user_id = session.get("user_id")
    if user_id and user_id in user_services:
        del user_services[user_id]

    return redirect(url_for("dashboard"))


# API Endpoints
@app.route("/api/jobs/status")
@token_required
@limiter.limit("30 per minute")
def api_jobs_status():
    """API endpoint to get job statuses for auto-update"""
    try:
        public_service, private_service, service_error = get_services_for_user()
        all_jobs = get_jobs_for_user(public_service, private_service, limit=30)

        status_data = []
        for job in all_jobs:
            status_data.append({
                "job_id": job["job_id"],
                "status": job["status"],
                "status_class": job["status_class"],
                "estimated_start": job.get("estimated_start")
            })

        return jsonify({"success": True, "jobs": status_data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/backends/status")
@token_required
@limiter.limit("30 per minute")
def api_backends_status():
    """API endpoint to get backend statuses"""
    try:
        public_service, private_service, service_error = get_services_for_user()
        backends = get_backends_for_user(public_service, private_service)

        return jsonify({
            "success": True,
            "backends": backends
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/jobs/<job_id>/timeline")
@token_required
@limiter.limit("30 per minute")
def api_job_timeline(job_id):
    """API endpoint to get job timeline"""
    try:
        timeline = historical_analyzer.get_job_timeline(job_id)
        return jsonify({"success": True, "timeline": timeline})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# Debug endpoint to check backend connectivity
@app.route("/debug/backends")
@token_required
def debug_backends():
    """Debug endpoint to check backend connectivity"""
    public_service, private_service, service_error = get_services_for_user()

    debug_info = {
        "has_public_service": public_service is not None,
        "has_private_service": private_service is not None,
        "service_error": service_error,
        "public_backends": [],
        "private_backends": [],
        "user_id": session.get("user_id"),
        "user_services_count": len(user_services),
        "user_backends_count": len(user_backends),
        "user_jobs_count": len(user_jobs)
    }

    try:
        if public_service:
            debug_info["public_backends"] = [b.name for b in public_service.backends()]
    except Exception as e:
        debug_info["public_error"] = str(e)

    try:
        if private_service:
            debug_info["private_backends"] = [b.name for b in private_service.backends()]
    except Exception as e:
        debug_info["private_error"] = str(e)

    return jsonify(debug_info)


# -----------------------------
# Error handlers
# -----------------------------
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error.html',
                           title="404 - Not Found",
                           error_code=404,
                           error_message="Page not found"), 404


@app.errorhandler(500)
def internal_error(error):
    return render_template('error.html',
                           title="500 - Server Error",
                           error_code=500,
                           error_message="Internal server error"), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    return render_template('error.html',
                           title="429 - Too Many Requests",
                           error_code=429,
                           error_message="Too many requests, please try again later"), 429


# Initialize the fetcher after other utilities
backend_data_fetcher = BackendDataFetcher()


# Add new routes
@app.route("/backends/<backend_name>")
@token_required
@limiter.limit("20 per minute")
def backend_details(backend_name):
    """Detailed view of a specific quantum backend"""
    try:
        # Get decrypted token for API calls
        encrypted_token = session.get("token")
        token = decrypt_token(encrypted_token) if encrypted_token else None

        # Fetch backend details
        backend_details = backend_data_fetcher.get_backend_details(backend_name, token)
        calibration_data = backend_data_fetcher.get_backend_calibration(backend_name, token)
        parameters_data = backend_data_fetcher.get_backend_parameters(backend_name, token)

        if not backend_details:
            flash(f"Could not fetch details for backend '{backend_name}'", "warning")
            return redirect(url_for("backends"))

        return render_template(
            "backend_details.html",
            title=f"Backend Details - {backend_name}",
            backend=backend_details,
            calibration=calibration_data,
            parameters=parameters_data,
            backend_name=backend_name
        )

    except Exception as e:
        flash(f"Error loading backend details: {str(e)}", "danger")
        return redirect(url_for("backends"))


@app.route("/api/backend/<backend_name>")
@token_required
@limiter.limit("30 per minute")
def api_backend_details(backend_name):
    """API endpoint to get backend details"""
    try:
        encrypted_token = session.get("token")
        token = decrypt_token(encrypted_token) if encrypted_token else None

        backend_details = backend_data_fetcher.get_backend_details(backend_name, token)

        if backend_details:
            return jsonify({"success": True, "backend": backend_details})
        else:
            return jsonify({"success": False, "error": "Backend not found"}), 404

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
# [file content end] (partial additions)

@app.route("/backends/<backend_name>/analytics")
@token_required
def backend_analytics(backend_name):
    token = decrypt_token(session["token"])
    crn = session.get("crn")

    fetcher = BackendDataFetcher()
    calibration = fetcher.get_backend_calibration(backend_name, token=token, instance=crn)
    parameters = fetcher.get_backend_parameters(backend_name, token=token, instance=crn)

    return render_template(
        "backend_analytics.html",
        title=f"{backend_name} Analytics",
        backend_name=backend_name,
        calibration=calibration,
        parameters=parameters
    )


# -----------------------------
# App entry
# -----------------------------
if __name__ == "__main__":
    # Use environment port or default to 5000
    port = int(os.environ.get("PORT", 5000))
    # Disable reloader to avoid issues with quantum sessions
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)


# [file name]: app.py (add this function)
# [file content begin] (additional function)
@app.route("/debug/jobs")
@token_required
def debug_jobs():
    """Debug endpoint to check jobs data flow"""
    public_service, private_service, service_error = get_services_for_user()

    # Get fresh jobs data
    jobs = get_jobs_for_user(public_service, private_service)

    debug_info = {
        "user_id": session.get("user_id"),
        "has_public_service": public_service is not None,
        "has_private_service": private_service is not None,
        "service_error": service_error,
        "jobs_count": len(jobs),
        "jobs": jobs,
        "user_jobs_cache": user_jobs.get(session.get("user_id"), []) if session.get("user_id") else []
    }

    return jsonify(debug_info)

