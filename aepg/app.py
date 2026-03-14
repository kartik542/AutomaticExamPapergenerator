import os
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from PyPDF2 import PdfReader
from dotenv import load_dotenv
import re
import PyPDF2
from flask import send_file
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageTemplate
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
import fitz  # type: ignore
import string
from sqlalchemy import func
import random
from datetime import datetime
import pytz  # Needed for timezone handling
from pytz import utc
from openai import OpenAI
import base64
import logging
import json

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# ✅ Import nltk before using it
import nltk
from nltk.corpus import stopwords
from nltk.tokenize import sent_tokenize

# ✅ Ensure nltk resources are available
nltk.data.path.append('./nltk_data')

try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    nltk.download('punkt', download_dir='./nltk_data')

try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords', download_dir='./nltk_data')


# Load environment variables
load_dotenv()

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROJECT_ID = os.getenv("PROJECT_ID")

if not OPENAI_API_KEY:
    logging.error("OpenAI API key not found. Set OPENAI_API_KEY in .env file.")
if not PROJECT_ID:
    logging.error("PROJECT_ID not found. Set PROJECT_ID in .env file.")

client = OpenAI(api_key=OPENAI_API_KEY, project=PROJECT_ID)
logging.info("OpenAI client initialized.")

# Flask config
app = Flask(__name__)
FLASK_ENV = os.getenv("FLASK_ENV", "development")
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///exam_paper.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
app.config["STATIC_UPLOADS"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")

# Session configuration for larger data
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload directories exist with proper permissions
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
os.makedirs(app.config["STATIC_UPLOADS"], exist_ok=True)

# Database setup
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ------------------ MODELS ------------------ #

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    role = db.Column(db.String(10), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question_text = db.Column(db.Text, nullable=False)
    marks = db.Column(db.Integer, nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    difficulty = db.Column(db.String(50), nullable=False)
    chapter = db.Column(db.String(100))
    topic = db.Column(db.String(100))
    question_type = db.Column(db.String(20))
    option_a = db.Column(db.String(200))
    option_b = db.Column(db.String(200))
    option_c = db.Column(db.String(200))
    option_d = db.Column(db.String(200))
    correct_option = db.Column(db.String(1))
    image = db.Column(db.String(200))
    bloom_level = db.Column(db.String(50))  # Bloom's Taxonomy level
    course_outcome = db.Column(db.String(50))  # Course Outcome (CO) field

class ExamPaper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    user = db.relationship('User', backref=db.backref('exam_papers', lazy=True))

class PreviousPaper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    filename = db.Column(db.String(150), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)


class ExamTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    total_marks = db.Column(db.Integer, nullable=False)
    time_duration = db.Column(db.Integer, nullable=False)  # in minutes
    sections = db.Column(db.JSON, nullable=False)  # Store section structure
    instructions = db.Column(db.Text)
    header_format = db.Column(db.Text)
    footer_format = db.Column(db.Text)



class ActivityLog(db.Model):
    __tablename__ = 'activity_log'
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), default=datetime.utcnow)
    logout_time = db.Column(db.DateTime(timezone=True), nullable=True)
    action = db.Column(db.String(50), nullable=True)  # Optional

    teacher = db.relationship('User', backref='activity_logs')

def log_activity(user_id, action):
    log = ActivityLog(teacher_id=user_id, action=action)
    db.session.add(log)
    db.session.commit()

# ------------------ UTILS ------------------ #


# Download NLTK stuff only once


STOPWORDS = set(stopwords.words('english'))



# -------------------- TEXT & PDF HANDLERS --------------------

def log_login(user_id):
    log = ActivityLog(
        teacher_id=user_id,
        timestamp=datetime.utcnow().replace(tzinfo=pytz.utc),
        action='login'
    )
    db.session.add(log)
    db.session.commit()

def log_logout(user_id):
    log = ActivityLog.query.filter_by(teacher_id=user_id).order_by(ActivityLog.timestamp.desc()).first()
    if log and not log.logout_time:
        log.logout_time = datetime.utcnow().replace(tzinfo=pytz.utc)
        log.action = 'logout'
        db.session.commit()

def extract_full_text(pdf_path):
    import fitz
    with fitz.open(pdf_path) as doc:
        return "".join([page.get_text("text") for page in doc])


def clean_paragraphs(text):
    import re
    paragraphs = re.split(r'(?<=[.?!])\s*\n+', text)
    return [p.strip() for p in paragraphs if len(p.strip()) > 40]


def extract_question_candidates(paragraphs):
    from nltk.tokenize import sent_tokenize
    questions = []
    for para in paragraphs:
        sentences = sent_tokenize(para)
        for sent in sentences:
            if sent.strip().endswith('?') and len(sent.split()) > 3:
                questions.append(sent.strip())
    return questions


# -------------------- SUBJECTIVE QUESTION GENERATION --------------------

def clean_text(text):
    """Clean and normalize text for better processing."""
    # Remove multiple spaces and newlines
    text = re.sub(r'\s+', ' ', text)
    # Normalize question numbers/bullets
    text = re.sub(r'(?<=\s)(\d+[\.\)]|\([a-zA-Z]\)|\•|\-)\s+', 'Q: ', text)
    # Normalize marks pattern
    text = re.sub(r'\[(\d+)\s*marks?\]|\(\s*(\d+)\s*marks?\)', r'[MARKS:\1\2]', text, flags=re.IGNORECASE)
    return text.strip()

def is_likely_question(text):
    """Determine if a given text is likely to be a question."""
    # Common question starters
    question_starters = [
        'explain', 'describe', 'what', 'how', 'why', 'when', 'where', 'which',
        'discuss', 'analyze', 'compare', 'contrast', 'evaluate', 'define',
        'calculate', 'solve', 'prove', 'derive', 'find', 'state', 'list'
    ]
    
    text_lower = text.lower().strip()
    
    # Check if text starts with common question words or contains question mark
    is_question = (
        any(text_lower.startswith(starter) for starter in question_starters) or
        '?' in text or
        text.strip().startswith('Q:') or
        re.match(r'^\d+\.|\([a-zA-Z]\)', text.strip())
    )
    
    # Check minimum length and word count
    has_substance = len(text.strip()) > 20 and len(text.split()) >= 3
    
    return is_question and has_substance

def extract_marks(text):
    """Extract marks from question text."""
    marks_pattern = r'\[MARKS:(\d+)\]'
    match = re.search(marks_pattern, text)
    if match:
        return int(match.group(1)), re.sub(marks_pattern, '', text).strip()
    return None, text

def identify_section_headers(text):
    """Identify potential section headers in the text."""
    section_patterns = [
        r'^SECTION[- ][A-Z](?:[:\.]|\s*$)',
        r'^PART[- ][A-Z](?:[:\.]|\s*$)',
        r'^(?:Section|Part)\s+(?:One|Two|Three|Four|Five)(?:[:\.]|\s*$)',
        r'^\d+\.\s*(?:Section|Part)(?:[:\.]|\s*$)'
    ]
    
    lines = text.split('\n')
    sections = []
    current_pos = 0
    
    for line in lines:
        line = line.strip()
        if any(re.match(pattern, line, re.IGNORECASE) for pattern in section_patterns):
            sections.append((current_pos, line))
        current_pos += len(line) + 1
    
    return sections

def structure_questions(text):
    """Structure the extracted text into organized questions."""
    # Clean the text first
    text = clean_text(text)
    
    # Split into potential questions
    sentences = nltk.sent_tokenize(text)
    
    structured_questions = []
    current_question = []
    
    for sent in sentences:
        sent = sent.strip()
        if is_likely_question(sent):
            # If we were building a previous question, save it
            if current_question:
                full_question = ' '.join(current_question)
                marks, question_text = extract_marks(full_question)
                structured_questions.append({
                    'text': question_text,
                    'marks': marks,
                    'type': 'subjective' if len(question_text.split()) > 10 else 'short'
                })
                current_question = []
            current_question.append(sent)
        elif current_question:
            # This sentence is likely a continuation of the current question
            current_question.append(sent)
    
    # Don't forget the last question
    if current_question:
        full_question = ' '.join(current_question)
        marks, question_text = extract_marks(full_question)
        structured_questions.append({
            'text': question_text,
            'marks': marks,
            'type': 'subjective' if len(question_text.split()) > 10 else 'short'
        })
    
    return structured_questions

def extract_key_information(text):
    """Extract key concepts and information from text."""
    # Remove extra whitespace and normalize text
    text = re.sub(r'\s+', ' ', text).strip()
    
    # Split into paragraphs
    paragraphs = text.split('\n\n')
    
    # Extract sentences that likely contain key information
    key_sentences = []
    for para in paragraphs:
        sentences = nltk.sent_tokenize(para)
        for sent in sentences:
            # Look for sentences with key indicators
            indicators = [
                'is', 'are', 'was', 'were', 'means', 'defined as',
                'refers to', 'consists of', 'comprises', 'includes',
                'example', 'definition', 'important', 'key', 'main',
                'fundamental', 'essential', 'primary', 'basic'
            ]
            if any(indicator in sent.lower() for indicator in indicators):
                key_sentences.append(sent.strip())
    
    return key_sentences

def generate_questions_from_text(text):
    """Generate different types of questions from the text content."""
    questions = []
    
    # Extract key information first
    key_sentences = extract_key_information(text)
    
    for sentence in key_sentences:
        # Clean the sentence
        sentence = sentence.strip()
        if len(sentence.split()) < 5:  # Skip very short sentences
            continue
            
        # Generate different types of questions
        
        # 1. Definition/Concept Questions
        if any(word in sentence.lower() for word in ['is', 'means', 'defined', 'refers']):
            concept = sentence.split('is')[0] if 'is' in sentence else sentence.split('means')[0]
            if len(concept.split()) <= 5:  # Check if it's a reasonable concept length
                questions.append({
                    'text': f"Define or explain the term '{concept.strip()}'.",
                    'marks': 3,
                    'type': 'short',
                    'source_text': sentence
                })
        
        # 2. Explanation Questions
        if len(sentence.split()) >= 10:
            questions.append({
                'text': f"Explain the following concept: {sentence}",
                'marks': 5,
                'type': 'subjective',
                'source_text': sentence
            })
        
        # 3. Analysis Questions
        if any(word in sentence.lower() for word in ['because', 'therefore', 'hence', 'thus', 'results', 'causes']):
            questions.append({
                'text': f"Analyze and discuss: {sentence}",
                'marks': 5,
                'type': 'subjective',
                'source_text': sentence
            })
        
        # 4. Compare/Contrast Questions
        if any(word in sentence.lower() for word in ['while', 'whereas', 'unlike', 'different', 'similar', 'comparison']):
            questions.append({
                'text': f"Compare and contrast the concepts mentioned in: {sentence}",
                'marks': 4,
                'type': 'subjective',
                'source_text': sentence
            })
    
    return questions

def analyze_content_structure(text):
    """Analyze the content structure to identify topics, definitions, and key concepts with Bloom's taxonomy."""
    structure = {
        'definitions': [],
        'concepts': [],
        'processes': [],
        'examples': [],
        'blooms_taxonomy': {
            'remember': [],
            'understand': [],
            'apply': [],
            'analyze': [],
            'evaluate': [],
            'create': []
        }
    }
    
    # Bloom's Taxonomy keywords
    bloom_keywords = {
        'remember': ['define', 'list', 'recall', 'name', 'identify', 'state', 'select', 'match', 'recognize', 'locate'],
        'understand': ['explain', 'interpret', 'describe', 'compare', 'discuss', 'predict', 'classify', 'summarize'],
        'apply': ['solve', 'implement', 'use', 'demonstrate', 'illustrate', 'operate', 'schedule', 'sketch'],
        'analyze': ['analyze', 'differentiate', 'examine', 'compare', 'contrast', 'investigate', 'categorize'],
        'evaluate': ['evaluate', 'judge', 'select', 'choose', 'decide', 'justify', 'verify', 'argue', 'recommend'],
        'create': ['create', 'design', 'develop', 'formulate', 'construct', 'propose', 'devise', 'compose']
    }
    
    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 20]
    
    for sentence in sentences:
        lower_sent = sentence.lower()
        
        # Classify according to Bloom's Taxonomy
        for level, keywords in bloom_keywords.items():
            if any(keyword in lower_sent for keyword in keywords):
                structure['blooms_taxonomy'][level].append(sentence)
        
        # Original classification
        if any(word in lower_sent for word in ['is', 'means', 'refers to', 'defined as']):
            structure['definitions'].append(sentence)
        elif any(word in lower_sent for word in ['concept', 'principle', 'theory', 'method']):
            structure['concepts'].append(sentence)
        elif any(word in lower_sent for word in ['process', 'step', 'procedure', 'how to', 'workflow']):
            structure['processes'].append(sentence)
        elif any(word in lower_sent for word in ['example', 'instance', 'such as', 'like']):
            structure['examples'].append(sentence)
    
    return structure

def determine_bloom_level(question_text):
    """Determine the Bloom's Taxonomy level of a question based on keywords and structure."""
    bloom_keywords = {
        'remember': ['define', 'list', 'recall', 'name', 'identify', 'state', 'select', 'match', 'recognize', 'locate'],
        'understand': ['explain', 'interpret', 'describe', 'compare', 'discuss', 'predict', 'classify', 'summarize'],
        'apply': ['solve', 'implement', 'use', 'demonstrate', 'illustrate', 'operate', 'schedule', 'sketch'],
        'analyze': ['analyze', 'differentiate', 'examine', 'compare', 'contrast', 'investigate', 'categorize'],
        'evaluate': ['evaluate', 'judge', 'select', 'choose', 'decide', 'justify', 'verify', 'argue', 'recommend'],
        'create': ['create', 'design', 'develop', 'formulate', 'construct', 'propose', 'devise', 'compose']
    }
    
    question_lower = question_text.lower()
    
    # Check each level's keywords
    for level, keywords in bloom_keywords.items():
        if any(keyword in question_lower for keyword in keywords):
            return level
    
    # Default to 'remember' if no specific level is identified
    return 'remember'

def extract_key_phrases(text, min_length=4):
    """Extract key phrases from text for better question generation."""
    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 20]
    phrases = []
    
    # Common technical terms to look for
    technical_terms = ['algorithm', 'function', 'method', 'class', 'object', 'variable', 
                      'database', 'system', 'process', 'interface', 'module', 'component',
                      'architecture', 'framework', 'pattern', 'design', 'implementation']
    
    for sentence in sentences:
        words = sentence.split()
        # Look for technical terms
        for i, word in enumerate(words):
            if word.lower() in technical_terms:
                phrase = ' '.join(words[max(0, i-1):min(len(words), i+3)])
                if len(phrase) > min_length:
                    phrases.append(phrase)
        
        # Get general phrases
        for i in range(len(words)):
            for j in range(2, 5):
                if i + j <= len(words):
                    phrase = ' '.join(words[i:i+j])
                    if len(phrase) > min_length and not any(char.isdigit() for char in phrase):
                        phrases.append(phrase)
    
    return sorted(list(set(phrases)), key=len, reverse=True)[:10]

def generate_basic_questions(text):
    """Generate questions locally without using OpenAI API"""
    questions = []
    
    # Get technical questions first
    questions.extend(generate_technical_questions(text))
    
    # Clean and prepare text
    text = text.strip()
    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 20]
    
    # Add general understanding questions
    if sentences:
        questions.append({
            'text': "What is the main purpose of this code?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Implements a web application functionality",
                'B': "Handles database operations",
                'C': "Processes user requests",
                'D': "Manages system resources"
            },
            'correct': 'A'
        })
    
    # Add implementation questions
    if 'class' in text.lower():
        questions.append({
            'text': "How are classes implemented in this code?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Using class definitions with methods",
                'B': "Using functional programming",
                'C': "Using procedural code",
                'D': "No class implementation"
            },
            'correct': 'A'
        })
    
    # Add error handling questions
    if 'except' in text:
        questions.append({
            'text': "How does the code handle errors?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Using try-except blocks",
                'B': "Using if-else statements",
                'C': "Using error codes",
                'D': "No error handling"
            },
            'correct': 'A'
        })
    
    # Remove duplicates
    seen = set()
    unique_questions = []
    for q in questions:
        q_hash = hash(q['text'])
        if q_hash not in seen:
            seen.add(q_hash)
            unique_questions.append(q)
    
    return unique_questions

def generate_conceptual_questions(concepts, num_questions=3):
    """Generate conceptual and definition-based questions."""
    questions = []
    for concept in concepts[:num_questions]:
        # Extract the concept term
        term = concept.split('is')[0].split('means')[0].strip()
        if len(term.split()) <= 5:  # Reasonable length for a concept
            questions.append({
                'text': f"Define and explain the concept of '{term}'.",
                'marks': 3,
                'type': 'short',
                'source_text': concept,
                'category': 'Conceptual'
            })
    return questions

def generate_analytical_questions(relationships, num_questions=3):
    """Generate analytical and reasoning questions."""
    questions = []
    for rel in relationships[:num_questions]:
        questions.append({
            'text': f"Analyze the following statement and explain the relationship described: '{rel}'",
            'marks': 5,
            'type': 'subjective',
            'source_text': rel,
            'category': 'Analytical'
        })
    return questions

def generate_application_questions(examples, num_questions=2):
    """Generate application-based questions."""
    questions = []
    for example in examples[:num_questions]:
        questions.append({
            'text': f"Based on the following example, explain how this concept can be applied in different scenarios: '{example}'",
            'marks': 4,
            'type': 'subjective',
            'source_text': example,
            'category': 'Application'
        })
    return questions

def generate_topic_based_questions(topics_dict, num_questions=2):
    """Generate comprehensive questions based on topics."""
    questions = []
    for topic, subtopics in topics_dict.items():
        if subtopics:
            # Generate a comprehensive question about the topic
            questions.append({
                'text': f"Explain in detail the key aspects of {topic}. Include main concepts and their significance.",
                'marks': 8,
                'type': 'subjective',
                'source_text': ' '.join(subtopics[:2]),
                'category': 'Comprehensive'
            })
            
            # Generate a comparative question if there are multiple subtopics
            if len(subtopics) >= 2:
                questions.append({
                    'text': f"Compare and contrast different aspects of {topic} discussed in the text.",
                    'marks': 6,
                    'type': 'subjective',
                    'source_text': topic,
                    'category': 'Comparative'
                })
    return questions[:num_questions]

class SyllabusUnit:
    def __init__(self, title, topics=None, hours=None):
        self.title = title
        self.topics = topics or []
        self.hours = hours
        self.subtopics = []
        self.learning_outcomes = []

def parse_syllabus_structure(text):
    """Parse syllabus content and identify units, topics, and learning outcomes."""
    # Common patterns in syllabus
    unit_patterns = [
        r'(?i)unit\s*[-:]?\s*\d+|module\s*[-:]?\s*\d+',
        r'(?i)section\s*[-:]?\s*\d+',
        r'(?i)chapter\s*[-:]?\s*\d+'
    ]
    
    # Split text into lines and clean
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    units = []
    current_unit = None
    current_topic = None
    
    for line in lines:
        # Check if line starts a new unit
        is_unit_header = any(re.search(pattern, line, re.IGNORECASE) for pattern in unit_patterns)
        
        if is_unit_header:
            if current_unit:
                units.append(current_unit)
            current_unit = SyllabusUnit(line)
            continue
        
        if current_unit:
            # Check for hours/credits pattern
            hours_match = re.search(r'(\d+)\s*(?:hours|hrs|credits)', line, re.IGNORECASE)
            if hours_match:
                current_unit.hours = int(hours_match.group(1))
                continue
            
            # Check for learning outcomes
            if any(keyword in line.lower() for keyword in ['outcome', 'objective', 'learn', 'understand', 'able to']):
                current_unit.learning_outcomes.append(line)
                continue
            
            # Check for bullet points or numbered items (likely topics/subtopics)
            if re.match(r'^[\d\.\-\•\*]\s+', line) or len(line) < 100:
                if line.count('.') <= 1:  # Main topic
                    current_topic = line
                    current_unit.topics.append(current_topic)
                else:  # Subtopic
                    current_unit.subtopics.append((current_topic, line))
    
    # Don't forget the last unit
    if current_unit:
        units.append(current_unit)
    
    return units

def generate_bloom_taxonomy_questions(topic, level, num_questions=1):
    """Generate questions based on Bloom's Taxonomy levels."""
    # Bloom's taxonomy verbs for different levels
    bloom_verbs = {
        'remember': ['define', 'describe', 'identify', 'list', 'name', 'state', 'write'],
        'understand': ['explain', 'interpret', 'summarize', 'classify', 'compare', 'discuss'],
        'apply': ['apply', 'demonstrate', 'solve', 'use', 'implement', 'show'],
        'analyze': ['analyze', 'differentiate', 'examine', 'compare', 'contrast', 'investigate'],
        'evaluate': ['evaluate', 'assess', 'criticize', 'judge', 'justify', 'support'],
        'create': ['create', 'design', 'develop', 'formulate', 'construct', 'propose']
    }
    
    questions = []
    verbs = bloom_verbs.get(level, bloom_verbs['understand'])
    
    for _ in range(num_questions):
        verb = random.choice(verbs)
        
        if level == 'remember':
            questions.append({
                'text': f"{verb.capitalize()} the concept of {topic}.",
                'marks': 3,
                'type': 'short',
                'category': 'Knowledge',
                'bloom_level': level
            })
        elif level == 'understand':
            questions.append({
                'text': f"{verb.capitalize()} the key principles of {topic}.",
                'marks': 4,
                'type': 'short',
                'category': 'Understanding',
                'bloom_level': level
            })
        elif level == 'apply':
            questions.append({
                'text': f"{verb.capitalize()} the concepts of {topic} to solve a real-world problem.",
                'marks': 5,
                'type': 'subjective',
                'category': 'Application',
                'bloom_level': level
            })
        elif level == 'analyze':
            questions.append({
                'text': f"{verb.capitalize()} the various components and their relationships in {topic}.",
                'marks': 6,
                'type': 'subjective',
                'category': 'Analysis',
                'bloom_level': level
            })
        elif level == 'evaluate':
            questions.append({
                'text': f"{verb.capitalize()} the effectiveness of different approaches in {topic}.",
                'marks': 8,
                'type': 'subjective',
                'category': 'Evaluation',
                'bloom_level': level
            })
        elif level == 'create':
            questions.append({
                'text': f"{verb.capitalize()} a new solution or approach for {topic}.",
                'marks': 10,
                'type': 'subjective',
                'category': 'Creation',
                'bloom_level': level
            })
    
    return questions

def generate_mcq_from_topic(topic, content):
    """Generate MCQ questions from a topic."""
    # If content is available, generate questions using AI for better quality
    if content and OPENAI_API_KEY:
        prompt = (
            f"Generate 2 diverse multiple-choice questions about '{topic}'. "
            f"Use the following content for context if helpful, but the questions should be about the topic itself: {content[:500]}\n"
            "Return a JSON object with a 'questions' key containing a list of objects, each with 'text', 'marks', 'type', 'category', 'bloom_level', "
            "'source_text', 'options' (a dict with a, b, c, d), and 'correct_option' (the key, e.g., 'a')."
        )
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.5,
            )
            raw_content = response.choices[0].message.content
            print("OpenAI raw response:", raw_content)  # For debugging
            try:
                response_data = json.loads(raw_content)
            except Exception as e:
                logging.error(f"JSON decode error: {e} | Content: {raw_content}")
                return []
            # Now handle both dict and list
            if isinstance(response_data, dict):
                mcq_questions = response_data.get("questions", [])
            elif isinstance(response_data, list):
                mcq_questions = response_data
            else:
                mcq_questions = []
            return mcq_questions
        except Exception as e:
            logging.error(f"OpenAI API error: {e}")
            return []

    # Fallback to basic, non-AI generation
    return [
        {
            'text': f"Which of the following best describes {topic}?",
            'marks': 2,
            'type': 'mcq',
            'category': 'Knowledge',
            'bloom_level': 'remember',
            'source_text': content[:200] if content else topic,
            'options': {
                'a': f"A basic definition of {topic}",
                'b': f"An advanced concept related to {topic}",
                'c': f"A practical application of {topic}",
                'd': "None of the above"
            },
            'correct_option': 'a'
        },
        {
            'text': f"In which scenario would you most likely apply the concept of {topic}?",
            'marks': 2,
            'type': 'mcq',
            'category': 'Application',
            'bloom_level': 'apply',
            'source_text': content[:200] if content else topic,
            'options': {
                'a': "A theoretical research scenario",
                'b': "A real-world practical application",
                'c': "A historical context analysis",
                'd': "All of the above"
            },
            'correct_option': 'd'
        }
    ]

def generate_questions_from_unit(unit):
    """Generate questions from a syllabus unit using different strategies."""
    questions = []
    
    for topic in unit.topics:
        # 2 marks questions - Basic concept questions
        questions.extend([{
            'text': f"Define the term {topic} briefly.",
            'marks': 2,
            'type': 'short',
            'category': 'Knowledge',
            'bloom_level': 'remember',
            'source_text': topic
        }])
        
        # MCQ questions - 2 marks each
        questions.extend(generate_mcq_from_topic(topic, unit.title))
        
        # 3 marks questions - Understanding and application
        questions.extend([
            {
                'text': f"Explain the key concepts of {topic}.",
                'marks': 3,
                'type': 'short',
                'category': 'Understanding',
                'bloom_level': 'understand',
                'source_text': topic
            },
            {
                'text': f"How would you apply {topic} in a practical scenario?",
                'marks': 3,
                'type': 'short',
                'category': 'Application',
                'bloom_level': 'apply',
                'source_text': topic
            }
        ])
        
        # 6 marks questions - Analysis and evaluation
        questions.extend([
            {
                'text': f"Analyze the importance and implications of {topic} in detail.",
                'marks': 6,
                'type': 'long',
                'category': 'Analysis',
                'bloom_level': 'analyze',
                'source_text': topic
            },
            {
                'text': f"Evaluate the effectiveness of different approaches in {topic}. Support your answer with examples.",
                'marks': 6,
                'type': 'long',
                'category': 'Evaluation',
                'bloom_level': 'evaluate',
                'source_text': topic
            }
        ])
    
    # Generate questions from learning outcomes
    for outcome in unit.learning_outcomes:
        # 6 marks question from learning outcome
        questions.append({
            'text': f"Explain how you would achieve the following learning outcome: {outcome}. Provide detailed examples and methodology.",
            'marks': 6,
            'type': 'long',
            'category': 'Application',
            'bloom_level': 'apply',
            'source_text': outcome
        })
    
    # If there are multiple topics, generate comparison questions
    if len(unit.topics) >= 2:
        topics = unit.topics[:2]
        questions.append({
            'text': f"Compare and contrast {topics[0]} and {topics[1]}. Discuss their similarities, differences, and relationships.",
            'marks': 6,
            'type': 'long',
            'category': 'Analysis',
            'bloom_level': 'analyze',
            'source_text': f"Comparison of {topics[0]} and {topics[1]}"
        })
    
    return questions

def extract_questions_from_pdf(path):
    """Process syllabus PDF and generate relevant questions."""
    try:
        logging.info(f"Starting PDF extraction from: {path}")  # Debug log
        
        # Extract text from PDF using PyMuPDF
        doc = fitz.open(path)
        text = ""
        
        for page in doc:
            blocks = page.get_text("blocks")
            blocks.sort(key=lambda b: (b[1], b[0]))  # Sort by vertical position
            for block in blocks:
                text += block[4] + "\n"
        
        logging.info(f"Extracted text length: {len(text)}")  # Debug log
        
        # Parse syllabus structure
        units = parse_syllabus_structure(text)
        
        if not units:  # Fallback to PyPDF2 if no units found
            logging.info("No units found with PyMuPDF, trying PyPDF2...")  # Debug log
            with open(path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"
                units = parse_syllabus_structure(text)
        
        print(f"Number of units found: {len(units)}")  # Debug log
        
        # Generate questions for each unit
        all_questions = {
            'Multiple Choice Questions (2 Marks)': [],
            'Short Answer Questions (2-3 Marks)': [],
            'Long Answer Questions (6 Marks)': [],
            'Analysis and Evaluation Questions (6 Marks)': []
        }
        
        for unit in units:
            unit_questions = generate_questions_from_unit(unit)
            for q in unit_questions:
                if q['type'] == 'mcq':
                    all_questions['Multiple Choice Questions (2 Marks)'].append(q)
                elif q['type'] == 'short':
                    all_questions['Short Answer Questions (2-3 Marks)'].append(q)
                elif q['type'] == 'long' and q['category'] in ['Analysis', 'Evaluation']:
                    all_questions['Analysis and Evaluation Questions (6 Marks)'].append(q)
                elif q['type'] == 'long':
                    all_questions['Long Answer Questions (6 Marks)'].append(q)
        
        # If no structured questions found, try direct text analysis
        if not any(questions for questions in all_questions.values()):
            print("No structured questions found, trying direct text analysis...")  # Debug log
            
            # Split text into paragraphs
            paragraphs = [p.strip() for p in text.split('\n\n') if len(p.strip()) > 50]
            
            for para in paragraphs[:5]:  # Process first 5 substantial paragraphs
                # Generate MCQs
                mcq_questions = generate_mcq_from_topic("General Topic", para)
                all_questions['Multiple Choice Questions (2 Marks)'].extend(mcq_questions)
                
                # Generate other question types using Bloom's taxonomy
                for level in ['remember', 'understand', 'apply']:
                    questions = generate_bloom_taxonomy_questions(para[:100], level, 1)
                    all_questions['Short Answer Questions (2-3 Marks)'].extend(questions)
                
                for level in ['analyze', 'evaluate']:
                    questions = generate_bloom_taxonomy_questions(para[:100], level, 1)
                    all_questions['Long Answer Questions (6 Marks)'].extend(questions)
        
        print(f"Total questions generated: {sum(len(q) for q in all_questions.values())}")  # Debug log
        return all_questions
        
    except Exception as e:
        print(f"Error in syllabus analysis: {e}")
        return {'General Questions': []}

# -------------------- UTILITIES --------------------

def estimate_difficulty_and_marks(text):
    length = len(text.split())
    keywords = ['why', 'how', 'explain', 'describe', 'compare']
    if length > 20 and any(k in text.lower() for k in keywords):
        return 'Hard', 5
    elif length > 12:
        return 'Medium', 3
    return 'Easy', 1
# -------------------- MAIN CONTROLLER --------------------

def handle_pdf_upload(pdf_path, mode='mcq'):
    """
    Handles both MCQ and Subjective question extraction from PDF.
    mode = 'mcq' or 'subjective'
    """
    if mode == 'mcq':
        text = extract_full_text(pdf_path)
        paragraphs = clean_paragraphs(text)
        top_paragraphs = sorted(paragraphs, key=lambda p: len(p.split()), reverse=True)[:3]

        total_mcqs = 0
        for para in top_paragraphs:
            mcqs = generate_mcqs_from_paragraph(para)
            for mcq in mcqs:
                options = mcq.get('options', {})
                # Normalize option keys to upper case
                options = {k.upper(): v.strip() for k, v in options.items() if k.upper() in ['A', 'B', 'C', 'D']}

                if len(options) == 4 and mcq.get('answer', '').upper() in options:
                    db.session.add(Question(
                        question_text=mcq['question'].strip(),
                        marks=2,
                        subject='General',
                        difficulty='medium',
                        question_type='mcq',
                        option_a=options['A'],
                        option_b=options['B'],
                        option_c=options['C'],
                        option_d=options['D'],
                        correct_option=mcq['answer'].upper()
                    ))
                    total_mcqs += 1
                else:
                    print(f"⚠️ Invalid MCQ or missing options: {mcq}")

        db.session.commit()
        flash(f'{total_mcqs} MCQs extracted and added.', 'success')

    elif mode == 'subjective':
        questions = extract_questions_from_pdf(pdf_path)
        for q_text in questions:
            difficulty, marks = estimate_difficulty_and_marks(q_text)
            db.session.add(Question(
                question_text=q_text,
                marks=marks,
                subject='General',
                difficulty=difficulty,
                question_type='subjective'
            ))

        db.session.commit()
        flash(f'{len(questions)} subjective questions extracted and added.', 'success')
# ------------------ ROUTES ------------------ #
@app.route('/')
def index():
    return render_template('index.html') if 'user_id' not in session else redirect(url_for('dashboard'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        data = request.form
        if data['password'] != data['confirm_password']:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('register'))

        if User.query.filter_by(email=data['email']).first():
            flash('Email already exists.', 'danger')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(data['password'])
        new_user = User(
            full_name=data['full_name'],
            email=data['email'],
            role=data['role'],
            department=data['department'],
            subject=data['subject'],
            password=hashed_password
        )
        db.session.add(new_user)
        db.session.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(email=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            session['user_id'] = user.id
            session['role'] = user.role
            log_login(user.id)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid credentials.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    user_id = session.get('user_id')
    if user_id:
        log_logout(user_id)
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard', methods=['GET'])
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    total_questions = Question.query.count()
    total_marks = db.session.query(func.sum(Question.marks)).scalar() or 0

    context = {
        'total_questions': total_questions,
        'total_marks': total_marks,
        'is_hod': user.role == 'HOD',
        'user': user
    }

    if user.role == 'HOD':
        IST = pytz.timezone('Asia/Kolkata')
        logs_query = ActivityLog.query.filter(ActivityLog.logout_time.isnot(None))

        # Filters
        teacher_name = request.args.get('teacher_name', '').strip()
        date_str = request.args.get('date', '').strip()

        if teacher_name:
            logs_query = logs_query.join(User).filter(User.full_name.ilike(f"%{teacher_name}%"))

        if date_str:
            try:
                selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                logs_query = logs_query.filter(func.date(ActivityLog.timestamp) == selected_date)
            except ValueError:
                flash("Invalid date format. Please use YYYY-MM-DD.", "warning")

        logs = logs_query.order_by(ActivityLog.timestamp.desc()).all()

        for log in logs:
            # Safe timezone conversion
            if log.timestamp.tzinfo is None:
                log.timestamp = pytz.utc.localize(log.timestamp)
            log.timestamp = log.timestamp.astimezone(IST)

            if log.logout_time:
                if log.logout_time.tzinfo is None:
                    log.logout_time = pytz.utc.localize(log.logout_time)
                log.logout_time = log.logout_time.astimezone(IST)

        context['logs'] = logs
        context['filter_name'] = teacher_name
        context['filter_date'] = date_str

    return render_template('dashboard.html', **context)

@app.route('/add_question', methods=['GET', 'POST'])
def add_question():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        form = request.form
        image = request.files.get('image')
        filename = secure_filename(image.filename) if image and image.filename else None
        if filename:
            image.save(os.path.join(app.config["STATIC_UPLOADS"], filename))

        # Safely get values from form with validation
        question_text = form.get('question_text')
        subject = form.get('subject')
        marks = form.get('marks')
        difficulty = form.get('difficulty')
        question_type = form.get('question_type')
        bloom_level = form.get('bloom_level')  # Get Bloom's level from form

        # Basic required field validation
        if not question_text or not subject or not marks or not difficulty or not question_type:
            flash('Please fill out all required fields.', 'danger')
            return redirect(url_for('add_question'))

        try:
            marks = int(marks)
        except ValueError:
            flash('Marks must be a number.', 'danger')
            return redirect(url_for('add_question'))

        # Set default Bloom's level based on question type if not provided
        if not bloom_level:
            if question_type == 'mcq':
                bloom_level = 'Remember'
            elif question_type == 'short':
                bloom_level = 'Understand'
            else:
                bloom_level = 'Analyze'

        new_q = Question(
            question_text=question_text,
            marks=marks,
            subject=subject,
            difficulty=difficulty,
            chapter=form.get('chapter'),
            topic=form.get('topic'),
            question_type=question_type,
            bloom_level=bloom_level,  # Add Bloom's level
            option_a=form.get('option_a') if question_type == 'mcq' else None,
            option_b=form.get('option_b') if question_type == 'mcq' else None,
            option_c=form.get('option_c') if question_type == 'mcq' else None,
            option_d=form.get('option_d') if question_type == 'mcq' else None,
            correct_option=form.get('correct_option') if question_type == 'mcq' else None,
            image=filename
        )
        db.session.add(new_q)
        db.session.commit()
        flash('Question added successfully.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('add_questions.html')

@app.route('/generate_exam', methods=['GET', 'POST'])
def generate_exam():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            form_data = request.form.to_dict()
            
            # Validate form data
            if not form_data.get('examTitle'):
                flash('Please fill in all required fields.', 'danger')
                return redirect(url_for('generate_exam'))

            # Get question counts and time duration
            mcq_count = int(form_data.get('mcqCount', 0))
            short_count = int(form_data.get('shortCount', 0))
            long_count = int(form_data.get('longCount', 0))
            time_duration = int(form_data.get('duration', 180))  # Get time duration from form

            if mcq_count + short_count + long_count == 0:
                flash('Please add at least one question.', 'danger')
                return redirect(url_for('generate_exam'))

            # Calculate total marks
            total_marks = (mcq_count * 2) + (short_count * 5) + (long_count * 10)

            # Initialize content list with header information
            content = [
                form_data['examTitle'],
                f"Time: {time_duration} minutes",
                f"Maximum Marks: {total_marks}",
                ""
            ]

            # Add instructions if provided
            if form_data.get('instructions'):
                content.extend([
                    "Instructions:",
                    form_data['instructions'],
                    ""
                ])

            # Section A: MCQs
            if mcq_count > 0:
                content.extend([
                    'Section A: Multiple Choice Questions',
                    f"({mcq_count} questions × 2 marks = {mcq_count * 2} marks)",
                    ""
                ])
                
                # Fetch MCQ questions
                mcq_questions = Question.query.filter_by(
                    question_type='mcq'
                ).order_by(func.random()).limit(mcq_count).all()

                if len(mcq_questions) < mcq_count:
                    flash(f'Not enough MCQ questions available. Found {len(mcq_questions)} MCQs but needed {mcq_count}.', 'danger')
                    return redirect(url_for('generate_exam'))

                # Add MCQ questions
                for i, q in enumerate(mcq_questions, 1):
                    bloom_level = q.bloom_level if q.bloom_level else "Remember"  # Default to Remember if not set
                    co = q.course_outcome if q.course_outcome else "CO1"  # Default to CO1 if not set
                    content.extend([
                        f"Q{i}. {q.question_text} [2 marks] [Bloom's Level: {bloom_level}] [{co}]",
                        f"a) {q.option_a}",
                        f"b) {q.option_b}",
                        f"c) {q.option_c}",
                        f"d) {q.option_d}",
                        ""
                    ])

            # Section B: Short Questions
            if short_count > 0:
                content.extend([
                    'Section B: Short Answer Questions',
                    f"({short_count} questions × 5 marks = {short_count * 5} marks)",
                    ""
                ])
                
                # Fetch short questions
                short_questions = Question.query.filter_by(
                    question_type='short'
                ).order_by(func.random()).limit(short_count).all()

                if len(short_questions) < short_count:
                    flash(f'Not enough short questions available. Found {len(short_questions)} but needed {short_count}.', 'danger')
                    return redirect(url_for('generate_exam'))

                # Add short questions
                for i, q in enumerate(short_questions, 1):
                    bloom_level = q.bloom_level if q.bloom_level else "Understand"  # Default to Understand if not set
                    co = q.course_outcome if q.course_outcome else "CO2"  # Default to CO2 if not set
                    content.extend([
                        f"Q{i}. {q.question_text} [5 marks] [Bloom's Level: {bloom_level}] [{co}]",
                        ""
                    ])

            # Section C: Long Questions
            if long_count > 0:
                content.extend([
                    'Section C: Long Answer Questions',
                    f"({long_count} questions × 10 marks = {long_count * 10} marks)",
                    ""
                ])
                
                # Fetch long questions
                long_questions = Question.query.filter_by(
                    question_type='long'
                ).order_by(func.random()).limit(long_count).all()

                if len(long_questions) < long_count:
                    flash(f'Not enough long questions available. Found {len(long_questions)} but needed {long_count}.', 'danger')
                    return redirect(url_for('generate_exam'))

                # Add long questions
                for i, q in enumerate(long_questions, 1):
                    bloom_level = q.bloom_level if q.bloom_level else "Analyze"  # Default to Analyze if not set
                    co = q.course_outcome if q.course_outcome else "CO3"  # Default to CO3 if not set
                    content.extend([
                        f"Q{i}. {q.question_text} [10 marks] [Bloom's Level: {bloom_level}] [{co}]",
                        ""
                    ])

            # Create exam paper record with metadata
            paper = ExamPaper(
                title=form_data['examTitle'],
                subject=form_data.get('subject', 'General'),
                content='\n'.join(content),
                created_by=session['user_id']
            )
            db.session.add(paper)
            db.session.commit()

            flash('Exam paper generated successfully!', 'success')
            return redirect(url_for('exam_paper_detail', paper_id=paper.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error generating exam paper: {str(e)}', 'danger')
            return redirect(url_for('generate_exam'))

    # GET request - show the form
    return render_template('generate_exam.html')

@app.route('/exam_paper/<int:paper_id>')
def exam_paper_detail(paper_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    paper = ExamPaper.query.get_or_404(paper_id)
    return render_template('exam_paper_detail.html', paper=paper)

@app.route('/view_exam_papers')
def view_exam_papers():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    papers = ExamPaper.query.filter_by(created_by=session['user_id']).all()
    return render_template('view_exam_papers.html', papers=papers)

@app.route('/question_bank')
def question_bank():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('question_bank.html', questions=Question.query.all())

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4


@app.route('/download_question_bank')
def download_question_bank():
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    questions = Question.query.all()

    for i, q in enumerate(questions, start=1):
        elements.append(Paragraph(f"<b>{i}. {q.question_text}</b>", styles["Normal"]))
        if q.question_type == "mcq":
            elements.append(Paragraph(f"A. {q.option_a}", styles["Normal"]))
            elements.append(Paragraph(f"B. {q.option_b}", styles["Normal"]))
            elements.append(Paragraph(f"C. {q.option_c}", styles["Normal"]))
            elements.append(Paragraph(f"D. {q.option_d}", styles["Normal"]))
            elements.append(Paragraph(f"<b>✅ Correct:</b> {q.correct_option}", styles["Normal"]))
        elements.append(Paragraph(f"<i>Subject:</i> {q.subject} | <i>Marks:</i> {q.marks} | <i>Difficulty:</i> {q.difficulty}", styles["Normal"]))
        elements.append(Spacer(1, 12))

    doc.build(elements)
    buffer.seek(0)

    return send_file(buffer, as_attachment=True, download_name="question_bank.pdf", mimetype='application/pdf')

@app.route('/edit_question/<int:question_id>', methods=['GET', 'POST'])
def edit_question(question_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    question = Question.query.get_or_404(question_id)
    if request.method == 'POST':
        form = request.form
        question.question_text = form['question_text']
        question.marks = int(form['marks'])
        question.subject = form['subject']
        question.difficulty = form['difficulty']
        question.chapter = form.get('chapter')
        question.topic = form.get('topic')
        question.question_type = form.get('question_type')
        question.option_a = form.get('option_a')
        question.option_b = form.get('option_b')
        question.option_c = form.get('option_c')
        question.option_d = form.get('option_d')
        question.correct_option = form.get('correct_option')
        db.session.commit()
        flash('Question updated.', 'success')
        return redirect(url_for('question_bank'))
    return render_template('edit_question.html', question=question)

@app.route('/delete_question/<int:question_id>', methods=['POST'])
def delete_question(question_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    question = Question.query.get_or_404(question_id)
    db.session.delete(question)
    db.session.commit()
    flash('Question deleted.', 'success')
    return redirect(url_for('question_bank'))

@app.route('/delete_all_questions', methods=['POST'])
def delete_all_questions():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    Question.query.delete()
    db.session.commit()
    flash('All questions deleted.', 'success')
    return redirect(url_for('question_bank'))

@app.route('/delete_paper/<int:paper_id>', methods=['POST'])
def delete_paper(paper_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    paper = ExamPaper.query.get_or_404(paper_id)
    db.session.delete(paper)
    db.session.commit()
    flash('Exam paper deleted.', 'success')
    return redirect(url_for('view_exam_papers'))

@app.route('/delete_all_papers', methods=['POST'])
def delete_all_papers():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    ExamPaper.query.filter_by(created_by=session['user_id']).delete()
    db.session.commit()
    flash('All exam papers deleted.', 'success')
    return redirect(url_for('view_exam_papers'))

@app.route('/upload_pdf_form')
def upload_pdf_form():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('upload_pdf.html')

@app.route('/upload_pdf', methods=['POST'])
def upload_pdf():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if 'pdf_file' not in request.files:
        flash('No file uploaded', 'error')
        return redirect(url_for('upload_pdf_form'))
    
    pdf_file = request.files['pdf_file']
    num_cos = request.form.get('num_cos', type=int)
    
    if not num_cos or num_cos < 1:
        flash('Please specify the number of Course Outcomes (COs)', 'error')
        return redirect(url_for('upload_pdf_form'))
    
    if not pdf_file or not pdf_file.filename:
        flash('No file selected', 'error')
        return redirect(url_for('upload_pdf_form'))
    if not pdf_file.filename.lower().endswith('.pdf'):
        flash('Only PDF files are allowed', 'error')
        return redirect(url_for('upload_pdf_form'))
    
    try:
        # Save the uploaded file
        filename = secure_filename(pdf_file.filename)
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        pdf_file.save(pdf_path)
        
        # Store number of COs in session for later use
        session['num_cos'] = num_cos
        session['pdf_filename'] = filename  # Store filename for display
        
        # Extract text from PDF
        text = extract_text_from_pdf(pdf_path)
        if not text:
            flash('Could not extract text from the PDF', 'error')
            return redirect(url_for('upload_pdf_form'))
        
        # Process the extracted text and generate questions
        questions = process_pdf_content(text)
        print(f"DEBUG: Questions generated: {len(questions) if questions else 0}")
        
        if not questions:
            flash('No questions could be generated from the PDF', 'error')
            return redirect(url_for('upload_pdf_form'))
        
        # Limit the number of questions to avoid session size issues (max 20 questions)
        if len(questions) > 20:
            questions = questions[:20]
            print(f"DEBUG: Limited questions to 20 to avoid session size issues")
        
        # Ensure questions are serializable for session storage
        serializable_questions = []
        for q in questions:
            serializable_q = {
                'text': str(q.get('text', ''))[:500],  # Limit text length
                'type': str(q.get('type', 'subjective')),
                'marks': int(q.get('marks', 5)),
                'bloom_level': str(q.get('bloom_level', 'Understand')),
                'course_outcome': str(q.get('course_outcome', 'CO1')),
                'difficulty': str(q.get('difficulty', 'medium'))
            }
            
            # Add MCQ options if present
            if q.get('type') == 'mcq' and 'options' in q:
                serializable_q['options'] = {str(k): str(v)[:200] for k, v in q['options'].items()}
                serializable_q['correct_option'] = str(q.get('correct_option', 'a'))
            
            serializable_questions.append(serializable_q)
        
        # Save questions to session
        try:
            session['extracted_questions'] = serializable_questions
            print(f"DEBUG: Questions stored in session: {len(session.get('extracted_questions', []))}")
            
            # Force session to be saved
            session.modified = True
            
            # Verify the data was stored
            stored_questions = session.get('extracted_questions')
            if not stored_questions:
                print("DEBUG: WARNING - Questions not stored in session!")
                flash('Error storing questions in session. Please try again.', 'error')
                return redirect(url_for('upload_pdf_form'))
                
        except Exception as session_error:
            print(f"DEBUG: Session storage error: {session_error}")
            flash('Error storing questions. Please try with a smaller PDF or fewer questions.', 'error')
            return redirect(url_for('upload_pdf_form'))
        
        # Clean up the uploaded file
        try:
            os.remove(pdf_path)
        except:
            pass  # Ignore cleanup errors
        
        flash(f'PDF processed successfully. {len(serializable_questions)} questions extracted. Please review the extracted questions.', 'success')
        return redirect(url_for('review_extracted_questions'))
        
    except Exception as e:
        print(f"DEBUG: Error in upload_pdf: {str(e)}")
        import traceback
        traceback.print_exc()
        flash(f'Error processing PDF: {str(e)}', 'error')
        return redirect(url_for('upload_pdf_form'))

def analyze_technical_content(text):
    """Analyze technical content to identify key components and concepts."""
    components = {
        'functions': [],
        'classes': [],
        'methods': [],
        'routes': [],
        'database': [],
        'api': []
    }
    
    lines = text.split('\n')
    for line in lines:
        line = line.strip().lower()
        if 'def ' in line:
            components['functions'].append(line)
        elif 'class' in line:
            components['classes'].append(line)
        elif '@app.route' in line:
            components['routes'].append(line)
        elif any(db_term in line for db_term in ['database', 'sql', 'query', 'model']):
            components['database'].append(line)
        elif any(api_term in line for api_term in ['api', 'request', 'response', 'json']):
            components['api'].append(line)
    
    return components

def generate_technical_questions(text):
    """Generate technical questions based on code content."""
    questions = []
    components = analyze_technical_content(text)
    
    # 1. Function Questions
    for func in components['functions'][:3]:
        questions.append({
            'text': f"What is the purpose of the function in: {func}?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Implements the described functionality",
                'B': "Handles error cases",
                'C': "Processes user input",
                'D': "Manages database connections"
            },
            'correct': 'A'
        })
    
    # 2. Route Questions
    for route in components['routes'][:3]:
        questions.append({
            'text': f"What does the route '{route}' handle?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Processes the specified HTTP request",
                'B': "Renders a template",
                'C': "Redirects to another page",
                'D': "Returns an error response"
            },
            'correct': 'A'
        })
    
    # 3. Database Questions
    if components['database']:
        questions.append({
            'text': "Which database operation is being performed?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Query execution",
                'B': "Data insertion",
                'C': "Table creation",
                'D': "Database connection"
            },
            'correct': 'A'
        })
    
    # 4. API Questions
    if components['api']:
        questions.append({
            'text': "How does the API handle requests?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Processes and returns JSON response",
                'B': "Returns error status",
                'C': "Redirects to another endpoint",
                'D': "Renders a template"
            },
            'correct': 'A'
        })
    
    return questions

def generate_basic_questions(text):
    """Generate questions locally without using OpenAI API"""
    questions = []
    
    # Get technical questions first
    questions.extend(generate_technical_questions(text))
    
    # Clean and prepare text
    text = text.strip()
    sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 20]
    
    # Add general understanding questions
    if sentences:
        questions.append({
            'text': "What is the main purpose of this code?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Implements a web application functionality",
                'B': "Handles database operations",
                'C': "Processes user requests",
                'D': "Manages system resources"
            },
            'correct': 'A'
        })
    
    # Add implementation questions
    if 'class' in text.lower():
        questions.append({
            'text': "How are classes implemented in this code?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Using class definitions with methods",
                'B': "Using functional programming",
                'C': "Using procedural code",
                'D': "No class implementation"
            },
            'correct': 'A'
        })
    
    # Add error handling questions
    if 'except' in text:
        questions.append({
            'text': "How does the code handle errors?",
            'type': 'mcq',
            'marks': 2,
            'options': {
                'A': "Using try-except blocks",
                'B': "Using if-else statements",
                'C': "Using error codes",
                'D': "No error handling"
            },
            'correct': 'A'
        })
    
    # Remove duplicates
    seen = set()
    unique_questions = []
    for q in questions:
        q_hash = hash(q['text'])
        if q_hash not in seen:
            seen.add(q_hash)
            unique_questions.append(q)
    
    return unique_questions



def extract_text_from_pdf(pdf_path):
    """Extract text from a PDF file using PyMuPDF, PyPDF2, or OCR as fallback."""
    text = ""
    # Try PyMuPDF
    try:
        import fitz
        doc = fitz.open(pdf_path)
        for page in doc:
            page_text = page.get_text("text")
            if page_text:
                text += page_text + "\n"
        doc.close()
        if len(text.strip()) > 50:
            return text
    except Exception as e:
        print(f"PyMuPDF extraction failed: {e}")
    # Try PyPDF2
    try:
        import PyPDF2
        with open(pdf_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        if len(text.strip()) > 50:
            return text
    except Exception as e:
        print(f"PyPDF2 extraction failed: {e}")
    # Try OCR as last resort
    try:
        from PIL import Image
        import pytesseract
        import pdf2image
        images = pdf2image.convert_from_path(pdf_path)
        for image in images:
            text += pytesseract.image_to_string(image) + "\n"
        if len(text.strip()) > 10:
            return text
    except Exception as e:
        print(f"OCR extraction failed: {e}")
    return text

def extract_questions_from_text(text):
    """
    Extracts as many likely questions as possible from the given text using multiple heuristics.
    Returns a list of question strings.
    """
    questions = set()
    lines = text.split('\n')
    question_starters = (
        'what', 'why', 'how', 'when', 'where', 'which', 'explain', 'describe',
        'discuss', 'analyze', 'compare', 'contrast', 'evaluate', 'define',
        'calculate', 'solve', 'prove', 'derive', 'find', 'state', 'list'
    )
    # 1. Ends with a question mark
    for line in lines:
        line_clean = line.strip()
        if not line_clean or len(line_clean) < 10:
            continue
        if line_clean.endswith('?'):
            questions.add(line_clean)
    # 2. Starts with a number, Q, or bullet
    for line in lines:
        line_clean = line.strip()
        if not line_clean or len(line_clean) < 10:
            continue
        if re.match(r'^(Q\d+\.|\d+\.|\([a-zA-Z]\)|\-|•)', line_clean):
            questions.add(line_clean)
    # 3. Starts with a question word
    for line in lines:
        line_clean = line.strip()
        if not line_clean or len(line_clean) < 10:
            continue
        if any(line_clean.lower().startswith(qw) for qw in question_starters):
            questions.add(line_clean)
    # 4. Extract from sentences: if sentence is long and contains question words, make a question
    sentences = re.split(r'(?<=[.!?])\s+', text)
    for sent in sentences:
        sent_clean = sent.strip()
        if len(sent_clean) > 20:
            for qw in question_starters:
                if qw in sent_clean.lower():
                    # Make a direct question
                    if not sent_clean.endswith('?'):
                        questions.add(f"{sent_clean}?")
                    else:
                        questions.add(sent_clean)
                    # Also make an 'Explain' or 'Discuss' question
                    questions.add(f"Explain: {sent_clean}")
                    questions.add(f"Discuss: {sent_clean}")
    # 5. If still not enough, generate 'explain' questions from all substantial sentences
    if len(questions) < 10:
        for sent in sentences:
            sent_clean = sent.strip()
            if len(sent_clean) > 30:
                questions.add(f"Explain: {sent_clean}")
    # 6. If still nothing, add a generic question
    if not questions:
        questions.add("Summarize the main content of the provided document.")
    return list(questions)

def generate_mcqs_from_paragraph(paragraph):
    prompt = f"""
You are an AI that generates multiple-choice questions for exams.

**Task**: From the given paragraph, generate exactly 2 MCQs.  
Each MCQ must be returned as a JSON object with:
- "question": The question text
- "options": A dictionary of 4 options, labeled "A", "B", "C", "D"
- "answer": The correct option key (e.g., "A")

Only return a list of 2 such JSON objects.

Example output:
[
  {{
    "question": "What is the capital of France?",
    "options": {{
      "A": "Paris",
      "B": "Rome",
      "C": "Berlin",
      "D": "Madrid"
    }},
    "answer": "A"
  }},
  {{
    "question": "What is 2 + 2?",
    "options": {{
      "A": "3",
      "B": "4",
      "C": "5",
      "D": "6"
    }},
    "answer": "B"
  }}
]

Now use this format to generate MCQs from the following paragraph:

\"\"\"{paragraph}\"\"\"
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=800
        )
        content = response.choices[0].message.content.strip()
        print("DEBUG GPT Output:\n", content)

        mcqs = json.loads(content)
        return mcqs

    except json.JSONDecodeError as e:
        logging.error(f"Could not decode GPT JSON output. Content: {content}. Error: {e}")
        return []
    except Exception as e:
        logging.error(f"AI-based MCQ generation failed for paragraph: {e}")
        return []

def generate_mcqs_from_text(text, num_mcqs=20):
    """
    Generate MCQs from text by identifying key sentences and creating options.
    """
    mcqs = []
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 30]
    keywords = [' is ', ' are ', ' was ', ' were ', ' means ', ' refers to ', ' defined as ', ' consists of ', ' includes ']
    used_questions = set()
    for sent in sentences:
        for kw in keywords:
            if kw in sent:
                parts = sent.split(kw, 1)
                if len(parts) == 2 and len(parts[0].split()) < 8:
                    answer = parts[1].strip().split()[0]
                    question = f"What{kw}{parts[0].strip()}?"
                    if question in used_questions or not answer or len(answer) < 3:
                        continue
                    used_questions.add(question)
                    # Generate distractors (random words from text, not the answer)
                    words = list(set([w for w in text.split() if w.lower() != answer.lower() and len(w) > 3]))
                    distractors = random.sample(words, min(3, len(words))) if len(words) >= 3 else ['Option1', 'Option2', 'Option3']
                    options = [answer] + distractors
                    random.shuffle(options)
                    mcqs.append({
                        'text': question,
                        'type': 'mcq',
                        'marks': 2,
                        'options': {chr(65+i): opt for i, opt in enumerate(options)},
                        'correct_option': chr(65 + options.index(answer))
                    })
    return mcqs[:num_mcqs]

def determine_course_outcome(question_text, num_cos):
    """
    Automatically determine the most appropriate Course Outcome (CO) for a question
    based on its content and keywords.
    """
    # Define keywords/patterns for each CO level
    co_keywords = {
        'CO1': ['define', 'list', 'recall', 'identify', 'basic', 'fundamental', 'introduction'],
        'CO2': ['explain', 'describe', 'discuss', 'illustrate', 'interpret', 'classify'],
        'CO3': ['apply', 'implement', 'solve', 'calculate', 'demonstrate', 'develop'],
        'CO4': ['analyze', 'compare', 'contrast', 'examine', 'investigate', 'differentiate'],
        'CO5': ['evaluate', 'assess', 'justify', 'judge', 'select', 'recommend'],
        'CO6': ['create', 'design', 'construct', 'plan', 'produce', 'invent']
    }
    
    # Convert question text to lowercase for matching
    text_lower = question_text.lower()
    
    # Count matches for each CO
    co_scores = {}
    for co_num in range(1, num_cos + 1):
        co_name = f'CO{co_num}'
        if co_name in co_keywords:
            score = sum(1 for keyword in co_keywords[co_name] if keyword in text_lower)
            co_scores[co_name] = score
    
    # If no matches found, assign based on question complexity
    if not any(co_scores.values()):
        words = len(text_lower.split())
        if words < 10:
            return f'CO1'
        elif words < 20:
            return f'CO{min(2, num_cos)}'
        else:
            return f'CO{min(3, num_cos)}'
    
    # Return the CO with the highest score
    return max(co_scores.items(), key=lambda x: x[1])[0]

def ai_generate_questions(text, num_questions=10, model="gpt-3.5-turbo"):
    if not OPENAI_API_KEY:
        raise Exception("OpenAI API key not found.")
    if not PROJECT_ID:
        raise Exception("OpenAI project ID not found.")

    prompt = (
        f"You are an expert exam question generator. Read the following academic text and generate {num_questions} diverse, meaningful questions "
        "that are directly relevant to the content. Avoid generic or vague questions. "
        "Include MCQs (with 4 options and the correct answer), short answer, and long answer questions. "
        "For MCQs, ensure all options are plausible and only one is correct. "
        "Distribute questions across Bloom's taxonomy levels (Remember, Understand, Apply, Analyze, Evaluate, Create) and assign a suitable Course Outcome (CO1, CO2, etc.).\n"
        "IMPORTANT: You must respond with ONLY a valid JSON array. No other text before or after the JSON.\n"
        "Each question object must have these fields: text, type (mcq/short/long), options (for MCQ), correct_option (for MCQ), marks, bloom_level, course_outcome.\n"
        "Example format:\n"
        "[\n"
        "  {\n"
        '    "text": "What is X?",\n'
        '    "type": "mcq",\n'
        '    "options": {"a": "Option A", "b": "Option B", "c": "Option C", "d": "Option D"},\n'
        '    "correct_option": "a",\n'
        '    "marks": 2,\n'
        '    "bloom_level": "Remember",\n'
        '    "course_outcome": "CO1"\n'
        "  }\n"
        "]\n"
        f"Text to generate questions from:\n{text[:2000]}\n\nGenerate exactly {num_questions} questions:"
    )

    try:
        print(f"DEBUG: Sending request to OpenAI with model: {model}")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant for generating exam questions. Always respond with valid JSON only."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=3000,
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        print(f"DEBUG: OpenAI raw response: {content[:500]}...")
        
        # Try to parse as JSON object first
        try:
            response_data = json.loads(content)
            # If it's a JSON object with a questions key, extract that
            if isinstance(response_data, dict) and 'questions' in response_data:
                questions = response_data['questions']
            elif isinstance(response_data, list):
                questions = response_data
            else:
                print(f"DEBUG: Unexpected response format: {type(response_data)}")
                return []
            
            print(f"DEBUG: Successfully parsed {len(questions)} questions from AI response")
            return questions
            
        except json.JSONDecodeError as e:
            print(f"DEBUG: JSON decode error: {e}")
            print(f"DEBUG: Raw content: {content}")
            
            # Try to extract JSON array from the response
            start = content.find('[')
            end = content.rfind(']')
            if start != -1 and end != -1:
                json_str = content[start:end+1]
                try:
                    questions = json.loads(json_str)
                    print(f"DEBUG: Successfully extracted JSON array with {len(questions)} questions")
                    return questions
                except json.JSONDecodeError as e2:
                    print(f"DEBUG: Failed to parse extracted JSON: {e2}")
                    return []
            else:
                print("DEBUG: Could not find JSON array markers in response")
                return []
                
    except Exception as e:
        print(f"DEBUG: OpenAI API call failed: {e}")
        logging.error(f"OpenAI GPT API call failed: {e}")
        return []

def ai_extract_text_from_image(image_path):
    if not OPENAI_API_KEY:
        raise Exception("OpenAI API key not found.")
    if not PROJECT_ID:
        raise Exception("OpenAI project ID not found.")

    with open(image_path, "rb") as img_file:
        image_data = img_file.read()

    try:
        response = client.chat.completions.create(
            model="gpt-4-vision-preview",
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": "Extract all readable text from this image."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(image_data).decode()}"}}
                ]}
            ],
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenAI Vision API failed: {e}")
        return ""
    
def process_pdf_content(text):
    """Process the extracted PDF text and generate as many meaningful questions as possible using GPT-3.5 (LLM), with fallback to heuristics."""
    try:
        print("Starting PDF content processing (AI-powered)...")
        print(f"Extracted text length: {len(text)}")
        print("First 200 characters of text:", text[:200])
        num_cos = session.get('num_cos', 3)  # Default to 3 if not specified
        questions = []
        
        # Try AI generation first
        try:
            print("DEBUG: Attempting AI question generation...")
            ai_questions = ai_generate_questions(text, num_questions=15)  # Reduced from 30 to 15
            print(f"DEBUG: AI returned {len(ai_questions) if ai_questions else 0} questions")
            
            if ai_questions and isinstance(ai_questions, list):
                print("DEBUG: Processing AI-generated questions...")
                for q in ai_questions:
                    q['bloom_level'] = determine_bloom_level(q.get('text', ''))
                    q['course_outcome'] = determine_course_outcome(q.get('text', ''), num_cos)
                questions = ai_questions
                print(f"DEBUG: Successfully processed {len(questions)} AI questions")
        except Exception as e:
            print(f"AI question generation failed: {e}")
            questions = []
        
        # Fallback to heuristic extraction if AI failed
        if not questions:
            print("Falling back to heuristic extraction...")
            units = parse_syllabus_structure(text)
            print(f"DEBUG: Found {len(units)} syllabus units")
            
            if units:
                for unit in units:
                    unit_questions = generate_questions_from_unit(unit)
                    print(f"DEBUG: Generated {len(unit_questions)} questions from unit: {unit.title}")
                    for question in unit_questions:
                        question['bloom_level'] = determine_bloom_level(question.get('text', ''))
                        question['course_outcome'] = determine_course_outcome(question.get('text', ''), num_cos)
                    questions.extend(unit_questions)
            else:
                print("DEBUG: No units found, using direct text analysis...")
                question_texts = extract_questions_from_text(text)
                print(f"DEBUG: Extracted {len(question_texts)} question texts")
                
                for q in question_texts:
                    questions.append({
                        'text': q,
                        'type': 'subjective',
                        'marks': 5,
                        'bloom_level': determine_bloom_level(q),
                        'course_outcome': determine_course_outcome(q, num_cos)
                    })
                
                mcqs = generate_mcqs_from_text(text, num_mcqs=15)  # Reduced from 30 to 15
                print(f"DEBUG: Generated {len(mcqs)} MCQs from text")
                for mcq in mcqs:
                    mcq['bloom_level'] = determine_bloom_level(mcq.get('text', ''))
                    mcq['course_outcome'] = determine_course_outcome(mcq.get('text', ''), num_cos)
                    questions.append(mcq)
        
        # Ensure we have at least some questions
        if not questions:
            print("DEBUG: No questions generated, creating fallback question...")
            questions = [{
                'text': 'Describe the content of the provided document.',
                'type': 'subjective',
                'marks': 5,
                'bloom_level': 'Understand',
                'course_outcome': 'CO1'
            }]
        
        # --- PERMANENT MCQ OPTIONS NORMALIZATION & CORRECT OPTION FIX ---
        for q in questions:
            if q.get('type') == 'mcq' and 'options' in q and q['options']:
                # Normalize all keys to lowercase for template compatibility
                q['options'] = {k.lower(): v for k, v in q['options'].items()}
                # Normalize correct_option to lowercase
                if 'correct_option' in q and q['correct_option']:
                    q['correct_option'] = q['correct_option'].lower()
                # Validate correct_option
                if q.get('correct_option') not in q['options']:
                    # If not valid, pick the first available option or leave blank
                    q['correct_option'] = next(iter(q['options']), '')
        
        print(f"Total questions generated: {len(questions)}")
        print(f"DEBUG: Question types: {[q.get('type', 'unknown') for q in questions]}")
        return questions
        
    except Exception as e:
        print(f"Error processing PDF content: {str(e)}\n")
        import traceback
        print(traceback.format_exc())
        return [{
            'text': 'Describe the content of the provided document.',
            'type': 'subjective',
            'marks': 5,
            'bloom_level': 'Understand',
            'course_outcome': 'CO1'
        }]

@app.route('/review_extracted_questions')
def review_extracted_questions():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get questions from session
    questions = session.get('extracted_questions')
    num_cos = session.get('num_cos', 0)
    
    print(f"DEBUG: review_extracted_questions - questions from session: {questions}")
    print(f"DEBUG: review_extracted_questions - num_cos from session: {num_cos}")
    print(f"DEBUG: review_extracted_questions - questions type: {type(questions)}")
    print(f"DEBUG: review_extracted_questions - questions length: {len(questions) if questions else 0}")
    
    if not questions:
        flash('No questions to review. Please upload a PDF first.', 'error')
        return redirect(url_for('upload_pdf_form'))
    
    # Generate list of COs based on num_cos
    course_outcomes = [f"CO{i+1}" for i in range(num_cos)]
    
    return render_template('review_questions.html', 
                         questions=questions, 
                         pdf_filename=session.get('pdf_filename'),
                         course_outcomes=course_outcomes)

@app.route('/save_reviewed_questions', methods=['POST'])
def save_reviewed_questions():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    try:
        selected_questions = request.form.getlist('selected_questions')
        questions = session.get('extracted_questions')
        
        for q_index in selected_questions:
            q_index = int(q_index)
            original_question = questions[q_index] if q_index < len(questions) else None
            
            # Get question details from form
            question_text = request.form.get(f'question_text_{q_index}')
            question_type = request.form.get(f'question_type_{q_index}')
            marks = int(request.form.get(f'marks_{q_index}') or 0)
            difficulty = request.form.get(f'difficulty_{q_index}')
            course_outcome = original_question.get('course_outcome') if original_question else f'CO1'
            
            # Create new question
            new_question = Question(
                question_text=question_text,
                question_type=question_type,
                marks=marks,
                difficulty=difficulty,
                subject='General',  # You might want to make this configurable
                course_outcome=course_outcome,  # Use the automatically assigned CO
                bloom_level=original_question.get('bloom_level') if original_question else None
            )
            
            # If it's an MCQ, add options
            if question_type == 'mcq':
                new_question.option_a = request.form.get(f'option_a_{q_index}')
                new_question.option_b = request.form.get(f'option_b_{q_index}')
                new_question.option_c = request.form.get(f'option_c_{q_index}')
                new_question.option_d = request.form.get(f'option_d_{q_index}')
                new_question.correct_option = request.form.get(f'correct_option_{q_index}')
            
            db.session.add(new_question)
        
        db.session.commit()
        
        # Clear the session data
        session.pop('extracted_questions', None)
        session.pop('pdf_filename', None)
        session.pop('num_cos', None)
        
        flash('Selected questions have been saved to the question bank.', 'success')
        return redirect(url_for('question_bank'))
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error saving questions: {str(e)}', 'error')
        return redirect(url_for('review_extracted_questions'))

@app.route('/download_exam_pdf/<int:paper_id>')
def download_exam_pdf(paper_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Get the exam paper
    paper = ExamPaper.query.get_or_404(paper_id)
    
    # Create a PDF buffer
    buffer = BytesIO()
    
    # Create the PDF document with proper margins
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=72,
        leftMargin=72,
        topMargin=72,
        bottomMargin=72
    )
    
    # Get the default style sheet and define custom styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=30,
        alignment=1  # Center alignment
    ))
    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        spaceAfter=20,
        spaceBefore=20,
        backColor=colors.HexColor('#f0f0f0'),
        borderPadding=5
    ))
    styles.add(ParagraphStyle(
        name='Question',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=10,
        leftIndent=20
    ))
    styles.add(ParagraphStyle(
        name='QuestionInfo',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.HexColor('#666666'),
        alignment=2  # Right alignment
    ))
    styles.add(ParagraphStyle(
        name='Option',
        parent=styles['Normal'],
        fontSize=11,
        leftIndent=40,
        spaceAfter=6
    ))
    
    # Create the document structure
    elements = []
    
    # Add title and exam details
    elements.append(Paragraph(paper.title, styles['CustomTitle']))
    elements.append(Paragraph(f"Subject: {paper.subject}", styles['Normal']))
    elements.append(Paragraph(f"Date: {paper.created_at.strftime('%d-%m-%Y')}", styles['Normal']))
    elements.append(Spacer(1, 20))
    
    # Process sections and questions
    sections = paper.content.split('\n')
    current_section = None
    current_question = None
    current_options = []
    
    for line in sections:
        if line.startswith('Section'):
            # If we have a previous question with options, add them
            if current_question and current_options:
                for opt in current_options:
                    elements.append(Paragraph(opt, styles['Option']))
                elements.append(Spacer(1, 10))
                current_options = []
            
            current_section = line
            elements.append(Paragraph(line, styles['SectionHeader']))
            elements.append(Spacer(1, 10))
            
        elif line.startswith('Q'):
            # If we have a previous question with options, add them
            if current_question and current_options:
                for opt in current_options:
                    elements.append(Paragraph(opt, styles['Option']))
                elements.append(Spacer(1, 10))
                current_options = []
            
            # Create a table for question layout with marks, Bloom's level, and CO on right
            data = []
            
            # Extract question number and text
            q_match = re.match(r'Q(\d+)\.\s+(.*?)(?:\s+\[|$)', line)
            if q_match:
                q_num = q_match.group(1)
                q_text = q_match.group(2)
                
                # Extract marks if present
                marks_match = re.search(r'\[(\d+)\s*marks?\]', line, re.IGNORECASE)
                marks = marks_match.group(1) if marks_match else ""
                
                # Extract Bloom's level if present
                bloom_match = re.search(r"\[Bloom's Level:\s*([^\]]+)\]", line)
                bloom = bloom_match.group(1) if bloom_match else ""
                
                # Extract CO if present
                co_match = re.search(r"\[(CO\d+)\]", line)
                co = co_match.group(1) if co_match else ""
                
                # Create question row with right-aligned info box
                question_table = Table([
                    [
                        Paragraph(f"Q{q_num}. {q_text}", styles['Question']),
                        Paragraph(
                            f'<para align="right"><font color="#198754"><b>Marks: {marks}</b></font><br/>'
                            f'<font color="#666666"><i>{bloom}</i></font><br/>'
                            f'<font color="#0d6efd"><b>{co}</b></font></para>',
                            styles['QuestionInfo']
                        )
                    ]
                ], colWidths=['*', 130])  # Make question column flexible, info column fixed width
                
                # Add table style for proper alignment
                question_table.setStyle(TableStyle([
                    ('ALIGN', (0, 0), (0, 0), 'LEFT'),
                    ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (0, 0), 0),
                    ('RIGHTPADDING', (1, 0), (1, 0), 0),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]))
                
                elements.append(question_table)
                current_question = line
        
        # Collect options for MCQs
        elif line.strip().startswith(('a)', 'b)', 'c)', 'd)')):
            current_options.append(line.strip())
        
        elif line.strip() and not line.startswith(('a)', 'b)', 'c)', 'd)')):
            elements.append(Paragraph(line, styles['Normal']))
    
    # Add any remaining options from the last question
    if current_question and current_options:
        for opt in current_options:
            elements.append(Paragraph(opt, styles['Option']))
        elements.append(Spacer(1, 10))
    
    # Build the PDF document
    doc.build(elements)
    
    # Prepare the response
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"exam_paper_{paper_id}.pdf",
        mimetype='application/pdf'
    )

@app.route('/upload_template', methods=['GET', 'POST'])
def upload_template():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        if 'template_file' not in request.files:
            flash('No file uploaded', 'error')
            return redirect(request.url)
        
        file = request.files['template_file']
        if not file or not file.filename:
            flash('No file selected', 'error')
            return redirect(request.url)
        if file and file.filename and file.filename.endswith('.pdf'):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Parse the template
            template_data = parse_template_from_pdf(filepath)
            if template_data:
                # Create new template
                template = ExamTemplate(
                    title=request.form.get('title', 'Untitled Template'),
                    subject=request.form.get('subject', 'General'),
                    created_by=session['user_id'],
                    total_marks=template_data['total_marks'],
                    time_duration=template_data['time_duration'],
                    sections=template_data['sections'],
                    instructions=template_data['instructions'],
                    header_format=template_data['header_format'],
                    footer_format=template_data['footer_format']
                )
                db.session.add(template)
                db.session.commit()
                
                # Clean up
                os.remove(filepath)
                
                flash('Template created successfully!', 'success')
                return redirect(url_for('view_templates'))
            
            flash('Could not parse template from PDF', 'error')
            return redirect(request.url)
    
    return render_template('upload_template.html')

@app.route('/view_templates')
def view_templates():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    templates = ExamTemplate.query.filter_by(created_by=session['user_id']).all()
    return render_template('view_templates.html', templates=templates)

@app.route('/generate_exam_from_template/<int:template_id>', methods=['GET', 'POST'])
def generate_exam_from_template(template_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    template = ExamTemplate.query.get_or_404(template_id)
    
    if request.method == 'POST':
        try:
            # Generate exam paper following template structure
            content = []
            
            # Add header
            content.extend([
                template.title,
                f"Subject: {template.subject}",
                f"Time: {template.time_duration} minutes",
                f"Maximum Marks: {template.total_marks}",
                "",
                template.instructions if template.instructions else "",
                ""
            ])
            
            # Generate sections following template
            for section in template.sections:
                content.append(section['title'])
                content.append("")
                
                # Get questions matching the section criteria
                questions = []
                for q_type, marks in zip(section['question_types'], section['marks_distribution']):
                    matching_questions = Question.query.filter_by(
                        subject=template.subject,
                        question_type=q_type,
                        marks=marks
                    ).all()
                    
                    if matching_questions:
                        questions.append(random.choice(matching_questions))
                
                # Add questions to content
                for i, q in enumerate(questions, 1):
                    if q.question_type == 'mcq':
                        content.extend([
                            f"Q{i}. {q.question_text} [{q.marks} marks] [Bloom's Level: {q.bloom_level}]",
                            f"a) {q.option_a}",
                            f"b) {q.option_b}",
                            f"c) {q.option_c}",
                            f"d) {q.option_d}",
                            ""
                        ])
                    else:
                        content.extend([
                            f"Q{i}. {q.question_text} [{q.marks} marks] [Bloom's Level: {q.bloom_level}]",
                            ""
                        ])
            
            # Create exam paper
            paper = ExamPaper(
                title=f"{template.subject} Exam - {datetime.now().strftime('%Y-%m-%d')}",
                subject=template.subject,
                content="\n".join(content),
                created_by=session['user_id']
            )
            db.session.add(paper)
            db.session.commit()
            
            flash('Exam paper generated successfully!', 'success')
            return redirect(url_for('exam_paper_detail', paper_id=paper.id))
            
        except Exception as e:
            flash(f'Error generating exam paper: {str(e)}', 'danger')
            return redirect(url_for('view_templates'))
    
    return render_template('generate_from_template.html', template=template)

def parse_template_from_pdf(pdf_path):
    """Parse an existing exam paper PDF to create a template."""
    try:
        text = extract_full_text(pdf_path)
        template_data = {
            'sections': [],
            'instructions': '',
            'header_format': '',
            'footer_format': '',
            'total_marks': 0
        }
        
        lines = text.split('\n')
        current_section = None
        
        for line in lines:
            # Extract header information
            if any(keyword in line.lower() for keyword in ['time:', 'duration:', 'marks:', 'instructions:']):
                if 'time' in line.lower() or 'duration' in line.lower():
                    template_data['time_duration'] = extract_time_duration(line)
                elif 'marks' in line.lower():
                    template_data['total_marks'] = extract_question_marks(line)
                elif 'instructions' in line.lower():
                    template_data['instructions'] += line + '\n'
            
            # Identify sections
            elif line.strip().lower().startswith(('section', 'part')):
                if current_section:
                    template_data['sections'].append(current_section)
                current_section = {
                    'title': line.strip(),
                    'question_types': [],
                    'marks_distribution': [],
                    'bloom_distribution': {}
                }
            
            # Process questions to understand structure
            elif line.strip().startswith(('q.', 'q)', 'question')):
                if current_section:
                    question_type = identify_question_type(line)
                    marks = extract_question_marks(line)
                    bloom_level = determine_bloom_level(line)
                    
                    current_section['question_types'].append(question_type)
                    current_section['marks_distribution'].append(marks)
                    current_section['bloom_distribution'][bloom_level] = \
                        current_section['bloom_distribution'].get(bloom_level, 0) + 1
        
        # Add the last section
        if current_section:
            template_data['sections'].append(current_section)
        
        return template_data
    
    except Exception as e:
        print(f"Error parsing template: {e}")
        return None

def extract_time_duration(text):
    """Extract time duration in minutes from text."""
    match = re.search(r'(\d+)\s*(hour|hr|minute|min)', text.lower())
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if 'hour' in unit or 'hr' in unit:
            return value * 60
        return value
    return 180  # default 3 hours

def extract_question_marks(text):
    """Extract marks from question text."""
    match = re.search(r'\[(\d+)\s*marks?\]|\((\d+)\s*marks?\)', text.lower())
    if match:
        return int(match.group(1) or match.group(2))
    return 0

def identify_question_type(text):
    """Identify the type of question based on its text and structure."""
    text_lower = text.lower()
    if any(option in text_lower for option in ['a)', 'b)', 'c)', 'd)']):
        return 'mcq'
    elif len(text_lower.split()) < 20:
        return 'short'
    return 'long'

@app.route('/delete_template/<int:template_id>', methods=['POST'])
def delete_template(template_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    template = ExamTemplate.query.get_or_404(template_id)
    
    # Check if the template belongs to the current user
    if template.created_by != session['user_id']:
        return jsonify({'error': 'Forbidden'}), 403
    
    try:
        db.session.delete(template)
        db.session.commit()
        return jsonify({'message': 'Template deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

def analyze_exam_format(file_path):
    """Analyze the uploaded exam paper format and extract its structure."""
    try:
        # Extract text from the uploaded file
        if file_path.lower().endswith('.pdf'):
            try:
                doc = fitz.open(file_path)
                text = ""
                for page in doc:
                    text += page.get_text("text")
                doc.close()
                
                # Debug print
                print(f"Extracted text length: {len(text)}")
                print("First 200 characters:", text[:200])
                
            except Exception as e:
                print(f"Error reading PDF: {str(e)}")
                return None
        else:
            print("Unsupported file format")
            return None

        if not text.strip():
            print("No text content found in the file")
            return None

        # Initialize format analysis results
        format_analysis = {
            'structure': {
                'has_header': False,
                'has_footer': False,
                'sections': [],
                'question_patterns': [],
                'total_marks': 0
            },
            'styling': {
                'numbering_style': None,
                'section_markers': []
            },
            'content': {
                'instructions_present': False,
                'time_duration': None,
                'subject': None,
                'exam_title': None
            }
        }

        # Split into lines and clean
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        
        # Debug print
        print(f"Number of lines found: {len(lines)}")

        # Analyze header information (first 10 non-empty lines)
        header_lines = lines[:10]
        for i, line in enumerate(header_lines):
            line_lower = line.lower()
            
            # Try to identify exam title (usually in first 3 lines)
            if i < 3 and len(line) > 5 and not format_analysis['content']['exam_title']:
                if any(word in line_lower for word in ['exam', 'test', 'assessment', 'paper']):
                    format_analysis['content']['exam_title'] = line
                    format_analysis['structure']['has_header'] = True
            
            # Look for subject
            if 'subject' in line_lower or 'course' in line_lower:
                parts = line.split(':')
                format_analysis['content']['subject'] = parts[-1].strip() if len(parts) > 1 else line
                format_analysis['structure']['has_header'] = True
            
            # Look for duration/time
            elif any(word in line_lower for word in ['time', 'duration', 'hours', 'minutes']):
                format_analysis['content']['time_duration'] = line
                format_analysis['structure']['has_header'] = True
                # Try to extract numeric duration
                time_match = re.search(r'(\d+)\s*(hour|hr|minute|min)', line_lower)
                if time_match:
                    value = int(time_match.group(1))
                    unit = time_match.group(2)
                    if 'hour' in unit or 'hr' in unit:
                        format_analysis['content']['duration_minutes'] = value * 60
                    else:
                        format_analysis['content']['duration_minutes'] = value
            
            # Look for total marks
            elif 'marks' in line_lower or 'total' in line_lower:
                marks_match = re.search(r'(\d+)', line)
                if marks_match:
                    format_analysis['structure']['total_marks'] = int(marks_match.group(1))

        # Look for instructions section
        in_instructions = False
        instructions = []
        
        # Analyze sections and questions
        current_section = None
        section_pattern = re.compile(r'^(?:SECTION|PART)\s+[A-Z]|^[A-Z]\.|^\d+\.\s*(?:Section|Part)|^(?:Section|Part)\s+\d+', re.IGNORECASE)
        question_pattern = re.compile(r'^(?:Q\.?|Question|^\d+\.|\([a-z]\)|\d+\))', re.IGNORECASE)
        
        for line in lines:
            line_lower = line.lower()
            
            # Check for instructions
            if 'instruction' in line_lower or 'note:' in line_lower:
                in_instructions = True
                format_analysis['content']['instructions_present'] = True
                instructions.append(line)
                continue
            elif in_instructions and (section_pattern.match(line) or question_pattern.match(line)):
                in_instructions = False
            elif in_instructions:
                instructions.append(line)
                continue

            # Detect sections
            if section_pattern.match(line):
                if current_section and current_section['question_count'] > 0:
                    format_analysis['structure']['sections'].append(current_section)
                
                current_section = {
                    'title': line,
                    'question_count': 0,
                    'marks_per_question': 0,
                    'total_marks': 0,
                    'question_type': 'unknown'
                }
                
                # Try to determine section type
                section_lower = line.lower()
                if any(word in section_lower for word in ['mcq', 'multiple choice', 'objective']):
                    current_section['question_type'] = 'mcq'
                elif any(word in section_lower for word in ['short', 'brief']):
                    current_section['question_type'] = 'short'
                elif any(word in section_lower for word in ['long', 'detailed', 'essay']):
                    current_section['question_type'] = 'long'
                
                format_analysis['styling']['section_markers'].append(line[:10])

            # Detect questions
            elif question_pattern.match(line):
                if current_section:
                    current_section['question_count'] += 1
                    
                    # Extract marks if present
                    marks_match = re.search(r'\[(\d+)\s*marks?\]|\((\d+)\s*marks?\)', line, re.IGNORECASE)
                    if marks_match:
                        marks = int(marks_match.group(1) or marks_match.group(2))
                        if current_section['marks_per_question'] == 0:
                            current_section['marks_per_question'] = marks
                        current_section['total_marks'] += marks
                    
                    # Detect if it's an MCQ by looking for options
                    if re.search(r'\([a-d]\)|\d\)', line, re.IGNORECASE) or \
                       any(next_line.strip().startswith(('a)', 'b)', 'c)', 'd)')) for next_line in lines[lines.index(line)+1:lines.index(line)+5]):
                        current_section['question_type'] = 'mcq'

        # Add the last section if exists
        if current_section and current_section['question_count'] > 0:
            format_analysis['structure']['sections'].append(current_section)

        # Store instructions if found
        if instructions:
            format_analysis['content']['instructions'] = '\n'.join(instructions)

        # Calculate total marks if not found in header
        if format_analysis['structure']['total_marks'] == 0:
            format_analysis['structure']['total_marks'] = sum(
                section['total_marks'] for section in format_analysis['structure']['sections']
            )

        # Determine question numbering style
        question_numbers = []
        for line in lines:
            match = re.match(r'^(Q\.?\s*\d+|Question\s*\d+|\d+\.|\([a-z]\))', line)
            if match:
                question_numbers.append(match.group(1))
        
        if question_numbers:
            format_analysis['styling']['numbering_style'] = {
                'type': 'numeric' if any(c.isdigit() for c in question_numbers[0]) else 'alphabetic',
                'prefix': question_numbers[0][:2],  # Q., 1., etc.
                'examples': question_numbers[:3]
            }

        # Debug print final analysis
        print("Analysis complete. Sections found:", len(format_analysis['structure']['sections']))
        for section in format_analysis['structure']['sections']:
            print(f"Section: {section['title']}, Questions: {section['question_count']}, Marks per Q: {section['marks_per_question']}")

        return format_analysis

    except Exception as e:
        print(f"Error analyzing exam format: {str(e)}")
        import traceback
        traceback.print_exc()
        return None

@app.route('/analyze_format', methods=['POST'])
def analyze_format():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    if 'formatFile' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['formatFile']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Invalid file format. Please upload a PDF file.'}), 400

    try:
        # Ensure upload directory exists
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

        # Save the uploaded file temporarily
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        # Analyze the format
        analysis_result = analyze_exam_format(file_path)

        # Clean up the temporary file
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Warning: Could not remove temporary file: {e}")

        if not analysis_result:
            return jsonify({
                'error': 'Could not analyze the format. Please ensure the file contains valid exam paper content and is properly formatted.'
            }), 400

        # Validate the analysis result
        if not analysis_result['structure']['sections']:
            return jsonify({
                'error': 'No sections found in the exam paper. Please ensure the paper has clear section markers (e.g., "Section A", "Part 1", etc.)'
            }), 400

        # Check if we found basic exam information
        missing_info = []
        if not analysis_result['structure']['total_marks']:
            missing_info.append("total marks")
        if not analysis_result['content']['time_duration']:
            missing_info.append("duration")
        if not analysis_result['content']['subject']:
            missing_info.append("subject")

        # Add warnings about missing information
        if missing_info:
            analysis_result['warnings'] = f"Missing information: {', '.join(missing_info)}"

        return jsonify({
            'success': True,
            'analysis': analysis_result
        })

    except Exception as e:
        # Log the error for debugging
        print(f"Error in analyze_format: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Clean up the temporary file if it exists
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
            
        return jsonify({
            'error': f'An error occurred while analyzing the format: {str(e)}'
        }), 500

# ------------------ START APP ------------------ #
if __name__ == '__main__':
    with app.app_context():
        # Add the new columns if they don't exist
        inspector = db.inspect(db.engine)
        existing_columns = [col['name'] for col in inspector.get_columns('question')]
        
        if 'bloom_level' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(db.text('ALTER TABLE question ADD COLUMN bloom_level VARCHAR(50)'))
                conn.commit()
            print("Added bloom_level column to Question table")
            
        if 'course_outcome' not in existing_columns:
            with db.engine.connect() as conn:
                conn.execute(db.text('ALTER TABLE question ADD COLUMN course_outcome VARCHAR(50)'))
                conn.commit()
            print("Added course_outcome column to Question table")
        
        db.create_all()
    app.run(debug=True)

