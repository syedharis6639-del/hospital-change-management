import os
import re
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'hospital-change-secret-python')

# Standard Flask Session (Cookie-based, more reliable in sandboxed previews)
app.config['SESSION_PERMANENT'] = False
# No Session(app) needed for standard flask sessions

def get_db_connection():
    # Try MySQL (Production) - only if host is defined and not reaching localhost
    # On many Windows dev machines, 'localhost' triggers OSErrors if the service isn't perfect
    mysql_host = str(os.getenv('MYSQL_HOST', '')).lower()
    # Skip MySQL if host is localhost, 127.x, or empty - much safer for local VSCode dev
    if mysql_host and 'localhost' not in mysql_host and '127.0.0.1' not in mysql_host:
        try:
            import mysql.connector
            connection = mysql.connector.connect(
                host=mysql_host,
                # Force a conversion to string to prevent OSErrors on invalid inputs
                user=str(os.getenv('MYSQL_USER', 'root')),
                password=str(os.getenv('MYSQL_PASSWORD', '')),
                database=str(os.getenv('MYSQL_DATABASE', 'hospital_db')),
                connect_timeout=3
            )
            if connection.is_connected():
                return connection
        except Exception as e:
            print(f"[SYSTEM] MySQL Production connection skipped: {e}")

    # Local Fallback (SQLite) - Much more reliable for VSCode Previews
    try:
        import sqlite3
        # Check if we are in a drive like D:/ which might have permission locks
        db_path = os.path.join(os.path.dirname(__file__), 'hospital.db')
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        print(f"[ERROR] SQLite fallback failed: {e}")
        return None

# SQL query helper to normalize between MySQL and SQLite
def execute_query(cursor, query, params=None):
    # More robust detection for SQLite across different OS/Environments
    cursor_type = str(type(cursor)).lower()
    is_sqlite = 'sqlite' in cursor_type
    
    if is_sqlite:
        # SQLite adaptations
        query = query.replace('%s', '?')
        # SQLite uses 'AUTOINCREMENT' (no underscore) or just 'INTEGER PRIMARY KEY'
        # We replace the MySQL 'AUTO_INCREMENT' with an empty string as SQLite handles it internally
        query = query.replace('AUTO_INCREMENT', '') 
        query = query.replace('ENUM', 'VARCHAR') 
        query = query.replace('TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP', 'DATETIME DEFAULT CURRENT_TIMESTAMP')
        query = query.replace('NOW() - INTERVAL 1 DAY', "datetime('now', '-1 day')")
        query = query.replace('NOW()', "datetime('now')")
        query = query.replace('CAST(id AS CHAR)', 'CAST(id AS TEXT)')
        query = re.sub(r"VARCHAR\s*\([^)]+\)", "VARCHAR(255)", query)
    
    if params:
        cursor.execute(query, params)
    else:
        cursor.execute(query)
    return cursor

# Initialize Database Schema
def init_db():
    conn = get_db_connection()
    if not conn:
        print("[WARNING] Skipping DB Init - Connection Failed.")
        return
    
    cursor = conn.cursor()
    
    execute_query(cursor, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(255) UNIQUE,
            password VARCHAR(255),
            role VARCHAR(50),
            name VARCHAR(255)
        )
    """)

    execute_query(cursor, """
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(255),
            age INT,
            diagnosis TEXT,
            status VARCHAR(50) DEFAULT 'Stable',
            last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
    """)

    execute_query(cursor, """
        CREATE TABLE IF NOT EXISTS change_requests (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            patient_id INT,
            field VARCHAR(255),
            old_value TEXT,
            new_value TEXT,
            requested_by_id INT,
            status VARCHAR(50) DEFAULT 'Pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            action_type VARCHAR(50),
            FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
            FOREIGN KEY(requested_by_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    execute_query(cursor, """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTO_INCREMENT,
            change_request_id INT,
            action_by_id INT,
            action VARCHAR(255),
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            details TEXT,
            FOREIGN KEY(change_request_id) REFERENCES change_requests(id) ON DELETE SET NULL,
            FOREIGN KEY(action_by_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # Ensure default accounts exist with a known state
    print("[SYSTEM] Verifying default accounts...")
    
    # Define default users
    default_users = [
        ("admin", "Admin", "[ADMIN] Chief Administrator"),
        ("doctor", "Doctor", "[DOCTOR] Dr. Sarah Jenkins"),
        ("staff", "Staff", "[STAFF] Nurse Michael")
    ]
    
    try:
        import bcrypt
        hashed_pass = bcrypt.hashpw("123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    except (ImportError, Exception):
        print("[WARNING] bcrypt not found. Using insecure legacy seeding.")
        hashed_pass = "123"

    for username, role, name in default_users:
        execute_query(cursor, "SELECT COUNT(*) FROM users WHERE username = %s", (username,))
        row = cursor.fetchone()
        if not row or row[0] == 0:
            print(f"[SYSTEM] Creating missing account: {username}")
            execute_query(cursor, "INSERT INTO users (username, password, role, name) VALUES (%s, %s, %s, %s)", 
                           (username, hashed_pass, role, name))
        else:
            # Force update password for admin/doctor/staff even if they exist (prevents lockout on local dev)
            execute_query(cursor, "UPDATE users SET password = %s WHERE username = %s", (hashed_pass, username))
    
    # Ensure some baseline patients exist
    execute_query(cursor, "SELECT COUNT(*) FROM patients")
    patients_count = cursor.fetchone()
    if not patients_count or patients_count[0] == 0:
        execute_query(cursor, "INSERT INTO patients (name, age, diagnosis, status) VALUES (%s, %s, %s, %s)", 
                       ("John Doe", 45, "Hypertension", "Stable"))
        execute_query(cursor, "INSERT INTO patients (name, age, diagnosis, status) VALUES (%s, %s, %s, %s)", 
                       ("Jane Smith", 29, "Type 2 Diabetes", "Critical"))
        
    conn.commit()
    cursor.close()
    conn.close()

# --- HELPERS ---

def get_stats():
    conn = get_db_connection()
    if not conn: return {"patients": 0, "pending": "00", "logs24h": 0, "health": "99.9%"}
    
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
        
    def get_count(query):
        execute_query(cursor, query)
        row = cursor.fetchone()
        if not row: return 0
        if hasattr(row, 'keys'): # MySQL Dictionary or SQLite Row
            try: return row['count']
            except: return row[0]
        return row[0]

    patients = get_count("SELECT COUNT(*) as count FROM patients")
    pending = get_count("SELECT COUNT(*) as count FROM change_requests WHERE status = 'Pending'")
    logs = get_count("SELECT COUNT(*) as count FROM audit_logs WHERE timestamp > NOW() - INTERVAL 1 DAY")
    
    cursor.close()
    conn.close()
    return {
        "patients": patients,
        "pending": str(pending).zfill(2),
        "logs24h": logs,
        "health": "99.9%"
    }

@app.context_processor
def inject_globals():
    return {
        "user": session.get('user'),
        "currentPath": request.path,
        "stats": get_stats()
    }

@app.route('/health')
def health():
    return jsonify({"status": "up", "db": "operational" if get_db_connection() else "fallback"})

# --- DECORATORS ---

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def require_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user: return redirect(url_for('login'))
        if user['role'] != 'Admin': return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def require_medical(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        user = session.get('user')
        if not user: return redirect(url_for('login'))
        if user['role'] == 'Staff': return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@app.route('/')
def index():
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user'): return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        conn = get_db_connection()
        if not conn: return render_template('login.html', error="DB connection failed")
        try:
            cursor = conn.cursor(dictionary=True)
        except Exception:
            cursor = conn.cursor()
        
        execute_query(cursor, "SELECT * FROM users WHERE LOWER(username) = LOWER(%s)", (username,))
        user = cursor.fetchone()
        
        authenticated = False
        if user:
            # Normalize user to dict
            if not isinstance(user, dict):
                user = dict(user)
            
            p_hash = user['password']
            if isinstance(p_hash, memoryview): p_hash = p_hash.tobytes()
            if isinstance(p_hash, bytes): p_hash = p_hash.decode('utf-8')

            try:
                import bcrypt
                # Check if it's a bcrypt hash (usually starts with $2)
                if p_hash.startswith('$2'):
                    if bcrypt.checkpw(password.encode('utf-8'), p_hash.encode('utf-8')):
                        authenticated = True
                else:
                    # Fallback to plain text comparison for unhashed legacy/seeded accounts
                    if password == p_hash:
                        authenticated = True
            except (ImportError, ValueError):
                # Fallback if bcrypt missing or if checkpw fails on non-bcrypt strings
                if password == p_hash:
                    authenticated = True
        
        if authenticated:
            user.pop('password')
            session['user'] = user
            cursor.close()
            conn.close()
            return redirect(url_for('dashboard'))
        
        cursor.close()
        conn.close()
        return render_template('login.html', error="Invalid credentials")
    
    return render_template('login.html', error=None)

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@require_auth
def dashboard():
    search = request.args.get('search', '')
    conn = get_db_connection()
    if not conn: return "DB Error"
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    if search:
        query = "SELECT * FROM patients WHERE name LIKE %s OR diagnosis LIKE %s OR CAST(id AS CHAR) LIKE %s ORDER BY name ASC"
        execute_query(cursor, query, (f'%{search}%', f'%{search}%', f'%{search}%'))
    else:
        execute_query(cursor, "SELECT * FROM patients ORDER BY name ASC")
    
    rows = cursor.fetchall()
    patients = [dict(r) if not isinstance(r, dict) else r for r in rows]
    
    cursor.close()
    conn.close()
    return render_template('dashboard.html', patients=patients, searchTerm=search)

@app.route('/add-patient', methods=['GET', 'POST'])
@require_medical
def add_patient():
    if request.method == 'POST':
        p_id = request.form.get('id')
        name = request.form.get('name')
        age = request.form.get('age')
        diagnosis = request.form.get('diagnosis')
        status = request.form.get('status', 'Stable') or 'Stable'
        user = session.get('user')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if p_id:
                execute_query(cursor, "INSERT INTO patients (id, name, age, diagnosis, status) VALUES (%s, %s, %s, %s, %s)", 
                               (p_id, name, age, diagnosis, status))
            else:
                execute_query(cursor, "INSERT INTO patients (name, age, diagnosis, status) VALUES (%s, %s, %s, %s)", 
                               (name, age, diagnosis, status))
            
            patient_id = p_id or cursor.lastrowid
            execute_query(cursor, "INSERT INTO audit_logs (action_by_id, action, details) VALUES (%s, %s, %s)",
                           (user['id'], "Create Patient", f"Created patient {name} (ID: {patient_id}) with status {status}"))
            conn.commit()
            return redirect(url_for('dashboard'))
        except Exception as e:
            return render_template('add-patient.html', error=str(e))
        finally:
            cursor.close()
            conn.close()
            
    return render_template('add-patient.html', error=None)

@app.route('/edit-patient/<int:id>', methods=['GET', 'POST'])
@require_medical
def edit_patient(id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    if request.method == 'POST':
        field = request.form.get('field')
        new_value = request.form.get('new_value')
        user = session.get('user')
        
        execute_query(cursor, "SELECT * FROM patients WHERE id = %s", (id,))
        patient = cursor.fetchone()
        if not isinstance(patient, dict): patient = dict(patient)
        
        status = 'Approved' if user['role'] == 'Admin' else 'Pending'
        
        execute_query(cursor, """
            INSERT INTO change_requests (patient_id, field, old_value, new_value, requested_by_id, status, action_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (id, field, str(patient[field]), str(new_value), user['id'], status, 'Update'))
        request_id = cursor.lastrowid
        
        if status == 'Approved':
            valid_fields = ['name', 'age', 'diagnosis', 'status']
            if field in valid_fields:
                execute_query(cursor, f"UPDATE patients SET {field} = %s WHERE id = %s", (new_value, id))
                execute_query(cursor, """
                    INSERT INTO audit_logs (change_request_id, action_by_id, action, details) 
                    VALUES (%s, %s, %s, %s)
                """, (request_id, user['id'], "Approve Change", f"Auto-approved update to {field} for patient {patient['name']}"))
        
        conn.commit()
        cursor.close()
        conn.close()
        return redirect(url_for('dashboard'))

    execute_query(cursor, "SELECT * FROM patients WHERE id = %s", (id,))
    patient = cursor.fetchone()
    if not isinstance(patient, dict): patient = dict(patient)
    cursor.close()
    conn.close()
    return render_template('edit-patient.html', patient=patient)

@app.route('/delete-patient/<int:id>', methods=['POST'])
@require_admin
def delete_patient(id):
    user = session.get('user')
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    execute_query(cursor, "SELECT name FROM patients WHERE id = %s", (id,))
    patient = cursor.fetchone()
    if not isinstance(patient, dict): patient = dict(patient)
    execute_query(cursor, "DELETE FROM patients WHERE id = %s", (id,))
    
    execute_query(cursor, "INSERT INTO audit_logs (action_by_id, action, details) VALUES (%s, %s, %s)",
                   (user['id'], "Delete Patient", f"Deleted patient {patient['name']} (ID: {id})"))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('dashboard'))

@app.route('/requests')
@require_admin
def requests():
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    execute_query(cursor, """
        SELECT cr.*, p.name as patient_name, u.name as requested_by_name
        FROM change_requests cr
        JOIN patients p ON cr.patient_id = p.id
        JOIN users u ON cr.requested_by_id = u.id
        WHERE cr.status = 'Pending'
        ORDER BY cr.created_at DESC
    """)
    rows = cursor.fetchall()
    reqs = [dict(r) if not isinstance(r, dict) else r for r in rows]
    cursor.close()
    conn.close()
    return render_template('requests.html', requests=reqs)

@app.route('/requests/<int:id>/action', methods=['POST'])
@require_admin
def request_action(id):
    action = request.form.get('action') # Approve/Reject
    user = session.get('user')
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    execute_query(cursor, "SELECT cr.*, p.name as patient_name FROM change_requests cr JOIN patients p ON cr.patient_id = p.id WHERE cr.id = %s", (id,))
    req = cursor.fetchone()
    if not req: return redirect(url_for('requests'))
    if not isinstance(req, dict): req = dict(req)
    
    status = 'Approved' if action == 'Approve' else 'Rejected'
    execute_query(cursor, "UPDATE change_requests SET status = %s WHERE id = %s", (status, id))
    
    if status == 'Approved':
        valid_fields = ['name', 'age', 'diagnosis', 'status']
        if req['field'] in valid_fields:
            execute_query(cursor, f"UPDATE patients SET {req['field']} = %s WHERE id = %s", (req['new_value'], req['patient_id']))
    
    execute_query(cursor, "INSERT INTO audit_logs (change_request_id, action_by_id, action, details) VALUES (%s, %s, %s, %s)",
                   (id, user['id'], f"{action} Change", f"{action}d {req['field']} change for patient {req['patient_name']}"))
    
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('requests'))

@app.route('/audit')
@require_admin
def audit():
    sort = request.args.get('sort', 'desc')
    filter_action = request.args.get('action', '')
    filter_user = request.args.get('user', '')
    filter_start = request.args.get('start', '')
    filter_end = request.args.get('end', '')
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    query = "SELECT al.*, u.name as action_by_name FROM audit_logs al JOIN users u ON al.action_by_id = u.id WHERE 1=1"
    params = []
    
    if filter_action:
        query += " AND al.action = %s"
        params.append(filter_action)
    if filter_user:
        query += " AND u.name = %s"
        params.append(filter_user)
    if filter_start:
        query += " AND al.timestamp >= %s"
        params.append(filter_start)
    if filter_end:
        query += " AND al.timestamp <= %s"
        params.append(filter_end + " 23:59:59")
        
    query += f" ORDER BY al.timestamp {'ASC' if sort == 'asc' else 'DESC'} LIMIT 100"
    
    execute_query(cursor, query, tuple(params))
    rows = cursor.fetchall()
    logs = [dict(r) if not isinstance(r, dict) else r for r in rows]
    
    execute_query(cursor, "SELECT DISTINCT action FROM audit_logs")
    rows = cursor.fetchall()
    actions = [r[0] if not hasattr(r, 'keys') else r['action'] for r in rows]
    
    execute_query(cursor, "SELECT DISTINCT name FROM users")
    rows = cursor.fetchall()
    users = [r[0] if not hasattr(r, 'keys') else r['name'] for r in rows]
    
    cursor.close()
    conn.close()
    return render_template('audit.html', logs=logs, actions=actions, users=users, sort=sort, 
                           filterAction=filter_action, filterUser=filter_user, filterStart=filter_start, filterEnd=filter_end)

@app.route('/history')
@require_medical
def history_list():
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    execute_query(cursor, "SELECT * FROM patients ORDER BY name ASC")
    rows = cursor.fetchall()
    patients = [dict(r) if not isinstance(r, dict) else r for r in rows]
    cursor.close()
    conn.close()
    return render_template('history-list.html', patients=patients)

@app.route('/history/patient/<int:id>')
@require_medical
def patient_history(id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    execute_query(cursor, "SELECT * FROM patients WHERE id = %s", (id,))
    patient = cursor.fetchone()
    if not patient: return redirect(url_for('history_list'))
    if not isinstance(patient, dict): patient = dict(patient)
    
    execute_query(cursor, """
        SELECT cr.*, u.name as reviewer_name
        FROM change_requests cr
        LEFT JOIN audit_logs al ON al.change_request_id = cr.id AND al.action = 'Approve Change'
        LEFT JOIN users u ON al.action_by_id = u.id
        WHERE cr.patient_id = %s AND cr.status = 'Approved'
        ORDER BY cr.created_at DESC
    """, (id,))
    rows = cursor.fetchall()
    history = [dict(r) if not isinstance(r, dict) else r for r in rows]
    
    execute_query(cursor, """
        SELECT al.*, u.name as creator_name
        FROM audit_logs al
        JOIN users u ON al.action_by_id = u.id
        WHERE al.action = 'Create Patient' AND al.details LIKE %s
    """, (f"%{patient['name']}%(ID: {id})%",))
    creation_log = cursor.fetchone()
    if creation_log and not isinstance(creation_log, dict): creation_log = dict(creation_log)
    
    cursor.close()
    conn.close()
    return render_template('history-patient.html', patient=patient, history=history, creationLog=creation_log)

@app.route('/report/patient/<int:id>')
@require_medical
def patient_report(id):
    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
    except Exception:
        cursor = conn.cursor()
    
    execute_query(cursor, "SELECT * FROM patients WHERE id = %s", (id,))
    patient = cursor.fetchone()
    if not patient: return redirect(url_for('history_list'))
    if not isinstance(patient, dict): patient = dict(patient)
    
    execute_query(cursor, """
        SELECT cr.*, u.name as reviewer_name
        FROM change_requests cr
        LEFT JOIN audit_logs al ON al.change_request_id = cr.id AND al.action = 'Approve Change'
        LEFT JOIN users u ON al.action_by_id = u.id
        WHERE cr.patient_id = %s AND cr.status = 'Approved'
        ORDER BY cr.created_at DESC
    """, (id,))
    rows = cursor.fetchall()
    history = [dict(r) if not isinstance(r, dict) else r for r in rows]
    
    execute_query(cursor, """
        SELECT al.*, u.name as creator_name
        FROM audit_logs al
        JOIN users u ON al.action_by_id = u.id
        WHERE al.action = 'Create Patient' AND al.details LIKE %s
    """, (f"%{patient['name']}%(ID: {id})%",))
    creation_log = cursor.fetchone()
    if creation_log and not isinstance(creation_log, dict): creation_log = dict(creation_log)
    
    cursor.close()
    conn.close()
    return render_template('report-patient.html', 
                           patient=patient, history=history, 
                           creationLog=creation_log, 
                           generatedAt=datetime.now().isoformat())

if __name__ == '__main__':
    # Initialize Database inside main to prevent blocking module import
    try:
        init_db()
    except Exception as e:
        print(f"[SYSTEM] Bootstrap error: {e}")
        
    print("[SYSTEM] MediFlow Server starting on port 3000...")
    app.run(host='0.0.0.0', port=3000, debug=True)
