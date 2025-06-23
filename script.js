let score = 0;
const loadingScreen = document.getElementById('loadingScreen');
const gameArea = document.getElementById('gameArea');
const basket = document.getElementById('basket');
const fallingObject = document.getElementById('fallingObject');
const scoreDisplay = document.getElementById('score');
const gameWidth = gameArea.clientWidth;

function startGame() {
    let objectPosition = Math.floor(Math.random() * (gameWidth - 30));
    fallingObject.style.left = objectPosition + 'px';
    fallingObject.style.top = '0px';
    fallingObject.style.display = 'block';

    const dropspeed = 45

    const drop = setInterval(() => {
        let topPosition = parseInt(fallingObject.style.top);
        if (topPosition >= gameArea.clientHeight - 30) {
            clearInterval(drop);
            fallingObject.style.display = 'none';
            
            loadingScreen.innerHTML = "<h1>Game Over, loser!</h1>";
            setTimeout(() => {
                window.location.href = 'color2.html'; // Redirect to loading screen after delay
            }, 2000)
        } else {
            fallingObject.style.top = topPosition + 5 + 'px';

            // Check for catch
            if (topPosition + 30 >= gameArea.clientHeight - 20 && 
                objectPosition >= parseInt(basket.style.left) - 25 && 
                objectPosition <= parseInt(basket.style.left) + 25) {
                score++;
                scoreDisplay.innerText = 'score: ' + score;

                // Check if score has reached 10
                if (score >= 10) {
                    clearInterval(drop);
                    fallingObject.style.display = 'none';
                    
                    loadingScreen.innerHTML = "<h1>Congrats! You won</h1>";
                    setTimeout(() => {
                        window.location.href = 'color2.html'; // Redirect to loading screen after delay
                    }, 2000)
                } else {
                    clearInterval(drop);
                    fallingObject.style.display = 'none';
                    startGame();
                }
            }
        }
    }, dropspeed);
}


document.addEventListener('mousemove', (e) => {
    const gameRect = gameArea.getBoundingClientRect(); // Get the bounding rectangle

    let leftPosition = e.clientX - gameRect.left - 25; // Calculate the basket's position

    // Ensure it stays within bounds
    if (leftPosition < 0) leftPosition = 0;
    if (leftPosition > gameRect.width - 50) leftPosition = gameRect.width - 50;

    basket.style.left = leftPosition + 'px'; // Update the basket's position
});


// Start the game
startGame();