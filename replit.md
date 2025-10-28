# Overview

This is a Telegram bot application for managing transportation bookings and routes. The bot serves three user roles: regular users (passengers), drivers, and admins. It handles booking reservations, route management, and driver assignments through a conversational interface powered by the aiogram framework.

# User Preferences

Preferred communication style: Simple, everyday language.

# System Architecture

## Bot Framework
- **Technology**: aiogram (asynchronous Telegram bot framework)
- **Rationale**: Modern async Python framework providing robust state management and handler routing
- **State Management**: FSM (Finite State Machine) with MemoryStorage for conversation flows
- **Alternative Considered**: python-telegram-bot (listed in requirements.txt but not actively used in code)

## Role-Based Access Control
- **Three-tier permission system**:
  - **Admins**: Full system access (defined in config.py)
  - **Drivers**: Can manage routes and view bookings
  - **Users**: Can create and manage their own bookings
- **Implementation**: Simple role checking functions (`is_admin()`, `is_driver()`) that verify user IDs against stored lists
- **Admin Privilege**: Admins automatically inherit driver permissions

## Data Persistence
- **Storage Solution**: JSON file-based persistence
- **Files**:
  - `bookings.json`: User booking records
  - `drivers.json`: List of authorized driver telegram IDs
  - `routes.json`: Route schedules with driver assignments (keyed by "YYYY-MM-DD HH:MM Direction")
- **Rationale**: Lightweight solution suitable for small-to-medium scale deployments without database overhead
- **Pros**: Simple deployment, no external dependencies, human-readable data
- **Cons**: Not suitable for high-concurrency scenarios, limited query capabilities

## Conversation Flow Management
- **FSM Pattern**: Uses aiogram's built-in FSM (Finite State Machine) for multi-step interactions
- **Storage**: MemoryStorage for temporary state (non-persistent across restarts)
- **UI Components**: 
  - ReplyKeyboardMarkup for main menu navigation
  - InlineKeyboardMarkup for callback-based interactions
  - Standard cancel flow with "❌ Відмінити" button

## Configuration Management
- **Environment Variables**: BOT_TOKEN loaded from environment
- **Validation**: Application fails fast if required configuration is missing
- **Admin List**: Hardcoded in config.py (should be externalized for production)

# External Dependencies

## Telegram Bot API
- **Service**: Telegram Bot Platform
- **Purpose**: Primary user interface and communication channel
- **Authentication**: Bot token-based authentication

## Python Packages
- **aiogram**: Telegram bot framework (v3.x based on import patterns)
- **python-telegram-bot**: Listed in requirements but appears unused (potential cleanup needed)
- **python-dotenv**: Environment variable management (imported in config but `.env` loading not shown)

## Runtime Environment
- **Expected Platform**: Replit or similar cloud hosting
- **Environment Variables Required**:
  - `BOT_TOKEN`: Telegram bot authentication token
- **File System**: Requires write access for JSON data files