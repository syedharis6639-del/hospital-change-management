-- MySQL Schema Migration for Hospital Change Management System
-- Use this script to set up your MySQL database on a real server.

CREATE DATABASE IF NOT EXISTS hospital_db;
USE hospital_db;

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL,
    role ENUM('Admin', 'Doctor', 'Staff') NOT NULL,
    name VARCHAR(100) NOT NULL
);

CREATE TABLE patients (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    age INT NOT NULL,
    diagnosis TEXT NOT NULL,
    last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE change_requests (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_id INT NOT NULL,
    field VARCHAR(50) NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    requested_by_id INT NOT NULL,
    status ENUM('Pending', 'Approved', 'Rejected') DEFAULT 'Pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action_type ENUM('Update', 'Create', 'Delete') NOT NULL,
    FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
    FOREIGN KEY (requested_by_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE audit_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    change_request_id INT,
    action_by_id INT NOT NULL,
    action VARCHAR(100) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details TEXT,
    FOREIGN KEY (change_request_id) REFERENCES change_requests(id) ON DELETE SET NULL,
    FOREIGN KEY (action_by_id) REFERENCES users(id) ON DELETE CASCADE
);

-- INITIAL SEEDING --
-- NOTE: Passwords here are hashed using bcrypt (admin123, doctor123, staff123)
INSERT INTO users (username, password, role, name) VALUES 
('admin', '$2a$10$W2iE6xS5C.uN/7L0z8H2zeoE...[REPLACE WITH HASH]', 'Admin', 'Chief Administrator'),
('doctor', '$2a$10$W2iE6xS5C...[REPLACE WITH HASH]', 'Doctor', 'Dr. Sarah Jenkins');
