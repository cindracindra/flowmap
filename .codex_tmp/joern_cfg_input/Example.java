class Example {
    int count;

    void prepare() {}

    void run(boolean ready) {
        if (ready) {
            prepare();
        }
        count++;
    }
}
