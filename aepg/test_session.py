#!/usr/bin/env python3
"""
Simple test script to verify session storage functionality
"""

from flask import Flask, session
import json

app = Flask(__name__)
app.secret_key = "test-secret-key"

# Test data similar to what we're storing
test_questions = [
    {
        'text': 'What is computer architecture?',
        'type': 'mcq',
        'marks': 2,
        'bloom_level': 'Remember',
        'course_outcome': 'CO1',
        'difficulty': 'easy',
        'options': {'a': 'Option A', 'b': 'Option B', 'c': 'Option C', 'd': 'Option D'},
        'correct_option': 'a'
    },
    {
        'text': 'Explain the concept of CPU organization.',
        'type': 'subjective',
        'marks': 5,
        'bloom_level': 'Understand',
        'course_outcome': 'CO2',
        'difficulty': 'medium'
    }
]

print("Testing session storage...")

with app.test_request_context():
    # Store test data
    session['extracted_questions'] = test_questions
    session['num_cos'] = 3
    session['pdf_filename'] = 'test.pdf'
    
    # Force session to be saved
    session.modified = True
    
    # Retrieve and verify
    stored_questions = session.get('extracted_questions')
    stored_num_cos = session.get('num_cos')
    stored_filename = session.get('pdf_filename')
    
    print(f"Stored questions: {len(stored_questions) if stored_questions else 0}")
    print(f"Stored num_cos: {stored_num_cos}")
    print(f"Stored filename: {stored_filename}")
    
    if stored_questions and len(stored_questions) == len(test_questions):
        print("✅ Session storage test PASSED")
    else:
        print("❌ Session storage test FAILED")
        print(f"Expected {len(test_questions)} questions, got {len(stored_questions) if stored_questions else 0}")

print("Test completed.") 