#include <cassert>

int main()
{
#ifdef NDEBUG
    return 2;
#endif
    bool evaluated = false;
    assert((evaluated = true));
    return evaluated ? 0 : 1;
}
