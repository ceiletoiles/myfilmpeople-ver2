# MyFilmPeople – Project Documentation

## 1. Introduction
MyFilmPeople is a people-centric film discovery and tracking system that allows users to explore cinema through filmmakers rather than movies. Unlike traditional platforms that focus on films, this system emphasizes directors, actors, crew members, and studios, enabling users to follow individuals and discover content through their work.

---

## 2. Objectives
- Provide a system to track and follow film personalities.
- Enable discovery of movies through people rather than titles.
- Offer collaboration insights between filmmakers.
- Display upcoming films based on followed individuals.
- Reduce repetitive searching by organizing personalized data.

---

## 3. Core Features

### 3.1 Follow System
Users can follow individuals across multiple roles:
- Directors
- Actors
- Crew members
- Studios

Features:
- Auto-suggestions using TMDb API
- Manual entry using TMDb ID or name search
- Role selection during follow
- Profile picture auto-fetch with fallback support
- Personal notes for each followed entity

---

### 3.2 Home Page
The home page displays followed entities categorized into:
- Directors
- Actors
- Crew
- Companies/Studios

Each section includes:
- Profile image
- Name
- Role

---

### 3.3 Profile Page (Person/Company)
Displays detailed information about a selected entity.

#### Basic Details:
- Profile picture
- Name
- Role(s)
- Date of birth
- Biography

#### Filmography:
- Categorized by roles
- Highlights followed roles
- Movies displayed with posters
- Role prioritization (e.g., director first)

#### Additional Features:
- Upcoming films
- Handling missing posters using default images

---

### 3.4 Movie Page
Displays detailed movie information.

#### Sections:
- Backdrop and poster display
- Title and release year
- Runtime
- Rating (if available)
- Tagline (optional)
- Synopsis

#### Tabs:
- Cast
- Crew
- Details

#### Additional Details:
- Production companies
- Genres
- Languages
- Technical specifications
- Budget and revenue

---

### 3.5 Search Page
Allows users to search across:
- Movies
- Filmmakers
- Studios

Features:
- Auto-suggestions
- Categorized results
- Default images for missing data

---

### 3.6 Collaboration Finder
Allows users to identify movies where multiple selected people have worked together.

Process:
- Select two or more individuals
- System identifies shared projects
- Displays results with:
  - Movie poster
  - Title
  - Year
  - Roles of each person

---

### 3.7 Upcoming Films
Displays upcoming movies based on followed individuals.

Features:
- Categorized by roles
- Sorted by release date
- Handles unknown release dates (TBA)
- Displays role information per movie

---

### 3.8 User Profile Page
Displays user-related information:
- Username
- Email
- Number of followed entities

#### Sections:
- Followed Directors
- Followed Actors
- Followed Crew
- Followed Studios

Each entry includes:
- Profile picture
- Name
- Role

---

## 4. System Design

### 4.1 Data Sources
- TMDb API (primary data source)

### 4.2 Local Storage
Data is stored locally after fetching to:
- Reduce API calls
- Improve performance
- Enable customization

### 4.3 Database Collections (Suggested)
- Users
- People
- Movies
- Follows

---

## 5. Key Functional Logic

### 5.1 Follow Logic
- User selects a person
- System fetches data from TMDb
- Data is stored locally
- Role is assigned

### 5.2 Edit Logic
- Allows updating stored data
- Syncs with API when required

### 5.3 Display Logic
- Prioritize important roles
- Handle missing images with defaults
- Sort content dynamically

---

## 6. Advantages
- Unique people-first approach
- Personalized tracking system
- Collaboration analysis feature
- Efficient use of external APIs

---


## 8. Future Enhancements
- Recommendation system
- Social features (sharing, reviews)
- Advanced filtering
- AI-based suggestions

---

## 9. Conclusion
MyFilmPeople provides a structured and efficient way to explore cinema through its creators. By focusing on individuals rather than films, it offers a unique and insightful approach to film discovery and tracking.

